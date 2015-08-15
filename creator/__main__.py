# Copyright (C) 2015 Niklas Rosenstein
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

import creator.unit
import creator.utils
import creator.ninja

import argparse
import os
import glob
import subprocess
import sys
import traceback

from creator.utils import term_print


parser = argparse.ArgumentParser(prog='creator',
  description='Creator - Meta build system for ninja.')
parser.add_argument('-D', '--define', help='Define a global variable that '
  'is accessible to all unit scripts. If no value is specified, it will be '
  'set to an empty string.', default=[], action='append')
parser.add_argument('-M', '--macro', help='The same as -D/--define but '
  ' evaluates like a macro. Remember that backslashes must be escaped, etc.',
  default=[], action='append')
parser.add_argument('-i', '--unitpath', help='Add an additional path to '
  'search for unit scripts to the workspace. The environment variable '
  'CREATORPATH is taken into account automatically as the search path '
  'additionally to the built-in script path and the current directory.',
  default=[], action='append')
parser.add_argument('-u', '--unit', help='The identifier of the unit to '
  'take as the main build unit. If this argument is omitted, it will be '
  'determined from the files in the current directory. There must only be '
  'one unit in the current directory if the automatic detection is used.')
parser.add_argument('targets', metavar='target', nargs='*', help='One or '
  'more full or local target or task identifiers to execute. Ninja will be '
  'invoked separately for each specified target.')
parser.add_argument('-e', '--export', help='Export the build.ninja file '
  'only. The specified targets will be the default targets in the file. '
  'A warning will be printed if any non-targets (ie. tasks) are specified.',
  action='store_true')
parser.add_argument('-n', '--no-export', help='Force not to export new '
  'build definitions. Conflicts with -e/--export.', action='store_true')
parser.add_argument('-d', '--dry', help='Dry run the unit scripts, but '
  'do nothing more. Implies -n/--no-export.', action='store_true')
parser.add_argument('-o', '--output', help='Override the output file of '
  'the ninja build definitions. By default, the file will be created at '
  '<build.ninja>. If the <$NinjaOut> variable is specified in a unit, it '
  'will be used as the output file if this option is omitted.')
parser.add_argument('-A', '--absolute-paths', action='store_true',
  help='If this option is specified, Creator will use absolute paths '
  'wherever possible (behaviour < 0.0.4).')
parser.add_argument('-c', '--clean', help='Clean the output files of the '
  'specified targets or all output files if no targets are specified. '
  'Implies -n/--no-export.', action='store_true')
parser.add_argument('--clean-with-dependencies', help='Like -c/--clean, '
  'but also cleans all the dependencies of the specified targets. '
  'Implies -c/--clean.', action='store_true')
parser.add_argument('-v', '--verbose', help='Adds the `-v` option to '
  'the inja invokation.', action='store_true')
parser.add_argument('-a', '--args', help='Additional arguments for all '
  'invokations of <ninja> done by Creator.', nargs=argparse.REMAINDER,
  default=[])


def call_subprocess(args, workspace):
  workspace.info("running: " + ' '.join(creator.utils.quote(x) for x in args))
  return subprocess.call(args)


def complete_target_list(targets, for_target=None):
  """
  Given a list of *targets*, completes that list in the correct
  order given the dependencies of the specified targets to make
  sure all dependencies are met before executed the *targets*.
  """

  if for_target is None:
    for target in targets[:]:
      complete_target_list(targets, target)
  else:
    for dep in reversed(for_target.dependencies):
      if dep in targets:
        targets.remove(dep)
      targets.insert(0, dep)
      complete_target_list(targets, dep)


def main(argv=None):
  if argv is None:
    argv = sys.argv[1:]
  args = parser.parse_args(argv)

  if args.no_export and args.export:
    parser.error('conflicting options -n/--no-export and -e/--export')
  if args.dry and args.export:
    parser.error('conflicting options -d/--dry and -e/--export')
  if args.clean_with_dependencies:
    args.clean = True
  if args.clean and args.export:
    parser.error('conflicting options -c/--clean and -e/--export')
  if args.clean:
    args.no_export = True

  workspace = creator.unit.Workspace()
  creator.workspace = workspace
  workspace.use_absolute_paths = args.absolute_paths
  workspace.path.extend(args.unitpath)

  # Evaluate the Defines and Macros passed via the command line.
  for define in args.define:
    key, _, value = define.partition('=')
    if key:
      workspace.context[key] = creator.macro.TextNode(value)

  for macro in args.macro:
    key, _, value = macro.partition('=')
    if key:
      workspace.context[key] = value

  # If not Unit Identifier was specified on the command-line,
  # look at the current directory and use the only .crunit that
  # is in there.
  if not args.unit:
    if os.path.exists('.creator'):
      metadata = creator.utils.read_metadata('.creator')
      if not 'creator.unit.name' in metadata:
        workspace.error("'.creator' missing @creator.unit.name")
      args.unit = metadata['creator.unit.name']
      if not creator.utils.validate_identifier(args.unit):
        workspace.error("'.creator' invalid @creator.unit.name")
    else:
      files = glob.glob('*.creator') + glob.glob('*.crunit')
      if not files:
        workspace.error("no '*.creator' or '*.crunit' files in current dir")
      elif len(files) > 1:
        workspace.error("multiple '*.crunit' and/or '*.creator' files in "
          "the current directory, use -u/--unit to specify which.")
      args.unit = creator.utils.set_suffix(os.path.basename(files[0]), '')

  # Load the active unit and set up all targets.
  unit = workspace.load_unit(args.unit)
  workspace.setup_targets()

  # Exit if this is just a dry run.
  if args.dry:
    return 0

  # Figure the output path for the build definitions.
  if not args.output:
    args.output = unit.eval('$self:NinjaOut').strip()
    if args.output:
      args.output = workspace.normpath(args.output)
      dirname = os.path.dirname(args.output)
      if not os.path.isdir(dirname):
        os.makedirs(dirname)
      args.output = os.path.relpath(args.output)
  if not args.output:
      args.output = 'build.ninja'

  # Collect a list of all targets and tasks.
  targets = [unit.get_target(x) for x in args.targets]

  # We must not complete the target list if we're only cleaning
  # without dependencies.
  if not args.clean or args.clean_with_dependencies:
    complete_target_list(targets)

  # Collect a list of all targets that will be processed by Ninja.
  ninja_targets = [
    t.identifier for t in targets if isinstance(t, creator.unit.Target)]

  if args.export:
    # Print a warning for each specified non-buildable target.
    for target in targets:
      if isinstance(target, creator.unit.Task):
        workspace.info("warning: {0} is a task".format(target.identifier))

  # If there are not targets specified and there are no targets
  # in the workspace, we don't have to export a ninja.build file
  # nor invoke Ninja.
  if not targets and not ninja_targets:
    if not any(isinstance(t, creator.unit.Target) for t in workspace.all_targets()):
      args.dry = True
      args.no_export = True

  # If we have any buildable targets specified, no targets specified at
  # all or if we should only export the build definitions, do exactly that.
  if not args.no_export and (args.export or ninja_targets or not targets):
    workspace.info("exporting to: {0}".format(args.output))
    with open(args.output, 'w') as fp:
      creator.ninja.export(fp, workspace, unit, ninja_targets)
    if args.export:
      return 0

  # Clean the target output files if --clean or --clean-with-dependencies
  # is specified.
  if args.clean or args.clean_with_dependencies:
    if not targets:
      targets = workspace.all_targets()
    cleaned_files = 0
    for target in targets:
      if isinstance(target, creator.unit.Target):
        for entry in target.command_data:
          for filename in entry['outputs']:
            if os.path.isfile(filename):
              try:
                os.remove(filename)
                cleaned_files += 1
              except OSError:
                workspace.error("Could not remove '{}'.".format(filename))
    workspace.info('Cleaned {} files.'.format(cleaned_files))
    return 0

  ninja_args = ['ninja', '-f', args.output] + args.args
  if args.verbose:
    ninja_args.append('-v')

  # No targets specified on the command-line? Build it all.
  if not targets and not args.dry:
    return call_subprocess(ninja_args, workspace)
  else:
    # Run each target with its own call to ninja and the tasks in between.
    for target in targets:
      if isinstance(target, creator.unit.Task):
        workspace.info("running task '{0}'".format(target.identifier))
        target.func()
      elif isinstance(target, creator.unit.Target):
        ident = creator.ninja.ident(target.identifier)
        res = call_subprocess(ninja_args + [ident], workspace)
        if res != 0:
          return res

    return 0


if __name__ == "__main__":
  sys.exit(main())
