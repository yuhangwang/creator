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

import creator.macro
import creator.ninja
import creator.utils
import os
import shlex
import subprocess
import sys
import warnings
import weakref

from creator.macro import TextNode as raw
from creator.utils import term_print


class UnitNotFoundError(Exception):
  pass


class Workspace(object):
  """
  The *Workspace* is basically the root of a *Creator* build session.
  It manages loading unit scripts and contains the global macro context.

  Attributes:
    path (list of str): A list of directory names in which unit scripts
      are being searched for. The unit scripts will actually also be
      searched in subdirectories of the specified paths.
    context (ContextProvider): The global macro context.
    units (dict of str -> Unit): A dictionary that maps the full
      identifier of a :class:`Unit` to the actual object.
    statics (dict of str -> Unit): A dictionary that maps the full
      normalized filenames of static creator files.
  """

  def __init__(self):
    super().__init__()
    self.path = ['.']
    self.path.append(os.path.join(os.path.dirname(__file__), 'builtins'))
    self.path.extend(os.getenv('CREATORPATH', '').split(os.pathsep))
    self.context = WorkspaceContext(self)
    self.units = {}
    self.statics = {}

    # If the current user has a `.creator_profile` file in his
    # home directory, run that file.
    filename = os.path.join(os.path.expanduser('~'), '.creator_profile')
    if os.path.isfile(filename):
      self.run_static_unit(filename)

    # Cache for the metadata that was read from .creator files.
    self._metadata_cache = {}

  def info(self, *args, **kwargs):
    kwargs.setdefault('fg', 'cyan')
    term_print('==> creator:', *args, **kwargs)

  def warn(self, *args, **kwargs):
    kwargs.setdefault('fg', 'red')
    kwargs.setdefault('attr', ('bright',))
    term_print('==> creator:', *args, **kwargs)

  def run_static_unit(self, filename):
    """
    Executes the unit under the specified filename and returns it.
    The unit will not be re-run if it was already run.
    Args:
      filename (str): The filename of the unit to execute.
    Returns:
      Unit: The unit executed.
    """

    filename = creator.utils.normpath(filename)
    if filename in self.statics:
      return self.statics[filename]

    unit = Unit(os.path.dirname(filename), 'static|' + filename, self)
    self.statics[filename] = unit
    try:
      unit.run_unit_script(filename)
    except Exception:
      del self.statics[filename]
      raise

  def get_unit(self, identifier):
    """
    Returns:
      Unit: The unit by the *identifier*
    Raises:
      ValueError: If there is no unit with the specified *identifier*.
    """

    if identifier not in self.units:
      raise ValueError('no such unit', identifier)
    return self.units[identifier]

  def find_unit(self, identifier):
    """
    Searches for the filename of a unit in the search :attr:`path`.

    Args:
      identifier (str): The identifier of the unit to load.
    Returns:
      str: The path to the unit script.
    Raises:
      UnitNotFoundError: If the unit could not be found.
    """

    crunit_fn = identifier + '.crunit'
    def check_file(filename):
      if filename.endswith('.creator'):
        try:
          metadata = self._metadata_cache[filename]
        except KeyError:
          metadata = creator.utils.read_metadata(filename)
          if 'creator.unit.name' not in metadata:
            self.warn("'{0}' missing @creator.unit.name'".format(filename))
          self._metadata_cache[filename] = metadata

        if metadata.get('creator.unit.name') == identifier:
          return True
      elif os.path.basename(filename) == crunit_fn:
        return True
      return False

    # Check first and second level files in search path.
    for dirname in self.path:
      if not os.path.isdir(dirname):
        continue
      for item in os.listdir(dirname):
        item = os.path.join(dirname, item)
        if os.path.isfile(item) and check_file(item):
          return item
        elif not os.path.isdir(item):
          continue
        for second in os.listdir(item):
          second = os.path.join(item, second)
          if os.path.isfile(second) and check_file(second):
            return second

    raise UnitNotFoundError(identifier)

  def load_unit(self, identifier):
    """
    If the unit with the specified *identifier* is not already loaded,
    it will be searched and executed and saved in the :attr:`units`
    dictionary.

    Args:
      identifier (str): The identifier of the unit to load.
    Returns:
      Unit: The loaded unit.
    Raises:
      UnitNotFoundError: If the unit could not be found.
    """

    if identifier in self.units:
      return self.units[identifier]

    filename = self.find_unit(identifier)
    filename = os.path.abspath(filename)

    # Execute the .creator_profile file in the current directory.
    dirname = os.path.dirname(filename)
    profile = os.path.join(dirname, '.creator_profile')
    if os.path.isfile(profile):
      self.run_static_unit(profile)

    # Run the unit that we found.
    unit = Unit(os.path.dirname(filename), identifier, self)
    self.units[identifier] = unit
    try:
      unit.run_unit_script(filename)
    except Exception:
      del self.units[identifier]
      raise
    return unit

  def setup_targets(self):
    """
    Sets up all targets in the workspace.
    """

    for unit in self.units.values():
      for target in unit.targets.values():
        if not target.is_setup:
          target.do_setup()


class Unit(object):
  """
  A *Unit* represents a collection of macros and build targets. Each
  unit has a unique identifier and may depend on other units. All units
  in a :class:`Workspace` share the same global macro context and each
  has its own local context as well as a local mapping of unit aliases
  and target declarators.

  Note that this class is also used to execute static creator files.
  The Unit identifier starts with the text ``'static|'`` and is followed
  by the filename of the unit. Use :meth:`is_static()` to check if the
  unit is static.

  Attributes:
    project_path (str): The path of the units project directory.
    identifier (str): The full identifier of the unit.
    workspace (Workspace): The workspace the unit is associated with.
    context (ContextProvider): The local context of the unit.
    aliases (dict of str -> str): A mapping of alias names to fully
      qualified unit identifiers.
    targets (dict of str -> Target): A dictionary that maps the name of
      a target to the corresponding :class:`Target` or :class:`Task`
      object.
    scope (dict): A dictionary that contains the scope in which the unit
      script is being executed.
  """

  def __init__(self, project_path, identifier, workspace):
    super().__init__()
    self.project_path = project_path
    self.identifier = identifier
    self.workspace = workspace
    self.aliases = {'self': self.identifier}
    self.targets = {}
    self.context = UnitContext(self)
    self.scope = self._create_scope()

  def _create_scope(self):
    """
    Private. Creates a Python dictionary that acts as the scope for the
    unit script which can be executed with :meth:`run_unit_script`.
    """

    return {
      'unit': self,
      'workspace': self.workspace,
      'C': self.context,
      'G': self.workspace.context,
      'run_task': self.run_task,
      'append': self.append,
      'confirm': self.confirm,
      'define': self.define,
      'defined': self.defined,
      'e': self.eval,
      'eq': self.eq,
      'ne': self.ne,
      'eval': self.eval,
      'exit': sys.exit,
      'extends': self.extends,
      'info': self.info,
      'join': creator.utils.join,
      'load': self.load,
      'raw': creator.macro.TextNode,
      'split': creator.utils.split,
      'shell': self.shell,
      'shell_get': self.shell_get,
      'target': self.target,
      'task': self.task,
      'warn': self.warn,
      'ExitCodeError': creator.utils.Response.ExitCodeError,
    }

  def get_identifier(self):
    return self._identifier

  def set_identifier(self, identifier):
    if not isinstance(identifier, str):
      raise TypeError('identifier must be str', type(identifier))
    if not identifier.startswith('static|'):
      if not creator.utils.validate_identifier(identifier):
        raise ValueError('invalid unit identifier', identifier)
    self._identifier = identifier

  def get_workspace(self):
    return self._workspace()

  def set_workspace(self, workspace):
    if not isinstance(workspace, Workspace):
      raise TypeError('workspace must be Workspace instance', type(workspace))
    self._workspace = weakref.ref(workspace)

  def get_target(self, target):
    """
    Returns:
      (Target or Task)
    Raises:
      ValueError
    """

    namespace, target = creator.utils.parse_var(target)
    if namespace is None:
      namespace = self.identifier
      targets = self.targets
    else:
      targets = self.workspace.get_unit(namespace).targets

    if target not in targets:
      full_ident = creator.utils.create_var(namespace, target)
      raise ValueError('no such target', full_ident)

    return targets[target]

  def run_unit_script(self, filename):
    """
    Executes the Python unit script at *filename* for this unit.
    """

    with open(filename) as fp:
      code = compile(fp.read(), filename, 'exec', dont_inherit=True)
    self.scope['__file__'] = filename
    self.scope['__name__'] = '__creator__'
    exec(code, self.scope)

  def is_static(self):
    return self._identifier.startswith('static|')

  identifier = property(get_identifier, set_identifier)
  workspace = property(get_workspace, set_workspace)

  def run_task(self, task_name):
    """
    Invokes the task with the specified *task_name*. Namespace names will
    be resolved by this function or the local namespace is used if a
    relative identifier is specified.

    Args:
      task_name (str): The name of the task to invoke.
    """

    namespace, varname = creator.utils.parse_var(task_name)
    if namespace is None:
      targets = self.targets
    else:
      targets = self.workspace.get_unit(namespace).targets

    try:
      task = targets[task_name]
      if not isinstance(task, Task):
        raise KeyError(task_name)
    except KeyError:
      raise ValueError('no such task', task_name)

    return task.func()

  def append(self, name, value):
    # todo: This is a rather dirty implementation. :-)
    self.define(name, '${' + name + '}' + value)

  def confirm(self, text):
    """
    Asks the user for a confirmation via stdin after expanding the
    *text* and appending it with ``'[Y/n]``.

    Args:
      text (str): The text to print
    Returns:
      bool: True if the user said yes, False if he or she said no.
    """

    text = self.eval(text)
    while True:
      self.info('{0} [Y/n]'.format(text), color='red', end=' ')
      response = input().strip().lower()
      if response in ('y', 'yes'):
        return True
      elif response in ('n', 'no'):
        return False
      else:
        print("Please reply Yes or No.", end=' ')

  def define(self, name, value):
    self.context[name] = value

  def defined(self, name):
    """
    Returns:
      bool: True if a variable with the specified *name* is defined.
    """

    return self.context.has_macro(name)

  def eq(self, left, right):
    if isinstance(left, str):
      left = self.eval(left)
    elif isinstance(right, str):
      right = self.eval(right)
    return left == right

  def ne(self, left, right):
    return not self.eq(left, right)

  def eval(self, text, supp_context=None):
    """
    Evaluates *text* as a macro string in the units context.

    Args:
      text (str): The text to evaluate.
      supp_context (creator.macro.ContextProvider): A context that
        will be taken into account additionally to the stack frame
        and unit context or None.
    Returns:
      str: The result of the evaluation.
    """

    if supp_context:
      context = creator.macro.ChainContext(self.context)
      context.contexts.insert(0, supp_context)
    else:
      context = self.context
    macro = creator.macro.parse(text, context)
    return macro.eval(context, [])

  def extends(self, identifier):
    """
    Loads all the contents of the Unit with the specified *identifier*
    into the scope of this Unit and substitutes the context references
    in the original macros with the context of this unit.

    Args:
      identifier (str): The name of the unit to inherit from.
    Returns:
      Unit: The Unit matching the *identifier*.
    """

    unit = self.load(identifier)
    self.context.update(unit.context, context_switch=True)
    return unit

  def info(self, *args, **kwargs):
    kwargs['fg'] = kwargs.pop('color', 'cyan')

    items = []
    for arg in args:
      if isinstance(arg, str):
        arg = self.eval(arg)
      items.append(arg)

    creator.utils.term_print(
      '==> creator: [{0}]'.format(self.identifier), *items, **kwargs)

  def warn(self, *args, **kwargs):
    kwargs['fg'] = kwargs.pop('color', 'red')
    items = []
    for arg in args:
      if isinstance(arg, str):
        arg = self.eval(arg)
      items.append(arg)

    creator.utils.term_print(
      '==> creator: [{0}]'.format(self.identifier), *items, **kwargs)

  def load(self, identifier, alias=None):
    """
    Loads a unit script and makes it available globally. If *alias* is
    specified, an alias will be created in this unit that referers to
    the loaded unit.

    Args:
      identifier (str): The identifer of the unit to load.
      alias (str, optional): An alias for the unit inside this unit.
    Returns:
      Unit: The loaded unit.
    """

    unit = self.workspace.load_unit(identifier)
    if alias is not None:
      if not isinstance(alias, str):
        raise TypeError('alias must be str', type(alias))
      self.aliases[alias] = identifier
    return unit

  def shell(self, command, shell=True, cwd=None):
    """
    Runs *command* attached to the current terminal. *command* is
    expanded before it is used to spawn a process.

    Returns:
      int: The exit-code of the process.
    """

    command = self.eval(command)
    if not shell:
      command = shlex.split(command)
    return subprocess.call(command, shell=shell, cwd=cwd)

  def shell_get(self, command, shell=True, cwd=None):
    """
    Runs *command* in the shell and returns a :class:`creator.utils.Response`
    object. *command* is expanded before it is used to spawn a process.

    Returns:
      creator.utils.Response: The object that contains the response data.
    """

    command = self.eval(command)
    if not shell:
      command = shlex.split(command)
    return creator.utils.Response(command, shell=shell, cwd=cwd)

  def target(self, *requirements):
    """
    Wraps a Python function to be treated as a target to declare the
    build definitions. _\*requirements_ must be passed zero or more
    requirements that are to be built before the actual target is.

    Requirements for targets may only be targets, not tasks.

    Returns:
      callable: A decorator for a function that returns a :class:`Target`.
    """

    for item in requirements:
      if not isinstance(item, str) and not isinstance(item, BaseTarget):
        raise TypeError('requirement must be str or BaseTarget', type(item))

    def decorator(func):
      if not callable(func):
        raise TypeError('func must be callable', type(func))
      if func.__name__ in self.targets:
        raise ValueError('target "{0}" already exists'.format(func.__name__))
      def on_setup(*args, **kwargs):
        [target.requires(req) for req in requirements]
        func(*args, **kwargs)
      target = Target(self, func.__name__, on_setup, False)
      self.targets[func.__name__] = target
      return target

    return decorator

  def task(self, *requirements):
    """
    Wraps a Python function as a task which can be invoked by the
    command-line or required by another task. _\*requirements_ must be
    passed zero or more requirements to be built/executed before the
    actual task is executed.

    Requirements may be targets or tasks.

    Returns:
      callable: A decorator for a function that returns a :class:`Task`.
    """

    for item in requirements:
      if not isinstance(item, str) and not isinstance(item, BaseTarget):
        raise TypeError('requirement must be str or BaseTarget', type(item))

    def decorator(func):
      if not callable(func):
        raise TypeError('func must be callable', type(func))
      if func.__name__ in self.targets:
        raise ValueError('task name already reserved', func.__name__)
      def on_setup(*args, **kwargs):
        [task.requires(req) for req in requirements]
      task = Task(func, self, func.__name__, on_setup)
      self.targets[func.__name__] = task
      return func

    return decorator


class BaseTarget(object):

  def __init__(self, unit, name, on_setup=None, pass_self=True, args=(), kwargs=None):
    if not isinstance(unit, creator.unit.Unit):
      raise TypeError('unit must be creator.unit.Unit', type(unit))
    if not isinstance(name, str):
      raise TypeError('name must be str', type(name))
    if not creator.utils.validate_identifier(name):
      raise ValueError('name is not a valid identifier', name)
    if on_setup is not None and not callable(on_setup):
      raise TypeError('on_setup must be None or callable')
    super().__init__()
    self._unit = weakref.ref(unit)
    self._name = name
    self.dependencies = []
    self.listeners = []
    self.is_setup = False
    self.on_setup = on_setup
    self.pass_self = pass_self
    self.args = args
    self.kwargs = kwargs or {}

  @property
  def unit(self):
    return self._unit()

  @property
  def name(self):
    return self._name

  @property
  def identifier(self):
    return self._unit().identifier + ':' + self._name

  def acccept_requirement(self, target):
    """
    Called when a requirement is added via :meth:`requires` to ensure
    that the requirement can be accepted by the target.
    """

    return

  def requires(self, target):
    """
    Adds *target* as a dependency for this target. If the *target* is
    not already set-up, it will be by this function.

    Args:
      target (str or Target): The target to build before the other.
        If a string is passed, the target name is resolved in the
        workspace.
    """

    if isinstance(target, str):
      namespace, target = creator.utils.parse_var(target)
      if not namespace:
        namespace = self.unit.identifier
      target = self.unit.workspace.get_unit(namespace).get_target(target)
    elif not isinstance(target, Target):
      raise TypeError('target must be Target object', type(target))
    self.acccept_requirement(target)
    if not target.is_setup:
      target.do_setup()
    self.dependencies.append(target)

  def do_setup(self):
    """
    Set up the targets internal data or dependencies. Call the parent
    method after successful exit to set :attr:`is_setup` to True. Raise
    an exception if something fails.

    Raises:
      RuntimeError: If the target is already set-up.
    """

    if self.is_setup:
      raise RuntimeError('target "{0}" is already set-up'.format(self.identifier))

    for listener in self.listeners:
      listener(self, 'do_setup', None)

    if self.on_setup is not None:
      if self.pass_self:
        self.on_setup(self, *self.args, **self.kwargs)
      else:
        self.on_setup(*self.args, **self.kwargs)

    self.is_setup = True
    return True


class Target(BaseTarget):
  """
  This class represents one or multiple build targets under one common
  identifier. It contains all the information necessary to generate the
  required build commands.

  A target has a set-up phase which is invoked after all units were
  loaded and evaluated. After this phase is complete, the target should
  be completely filled with all data.

  Args:
    unit (creator.unit.Unit): The unit this target belongs to.
    name (str): The name of the target.
    on_setup (callable): A Python function that is called on set-up.
    pass_self (bool): True if the target should be passed as the first
      argument to *on_setup*, False if not.
    args (any): List of arguments passed to *on_setup*.
    kwargs (any): List of keyword arguments passed to *on_setup*.

  Attributes:
    unit (creator.unit.Unit): The unit this target belongs to.
    name (str): The name of the target.
    identifier (str): The identifier of the target, which is the
      units identifier and the targets name concatenated.
    is_setup (bool): True if the target is set-up, False if not.
    on_setup (callable)
    pass_self (bool)
    args (any)
    kwargs (any)
    listeners (list of callable): A list of functions listening to
      certain events of the target. The functions are invoked with
      the three arguments ``(target, event, data)``.
    command_data (list of dict): A list of build commands. Each entry
      is a dictionary with the keys ``'inputs', 'outputs', 'command',
      'auxiliary'``.

  Listener Events:
    - ``'do_setup'``: Sent when :meth:`do_setup` is called. There is
      no data for this event.
    - ``'build'``: Sent when :meth:`build` is called. The data for
      this event is a dictionary ``{'inputs': str, 'outputs': str,
        'command': str, 'auxiliary': [], 'each': bool}``. The listener
        is allowed to modify the event data. The auxiliary list can be
        filled with a list of files that are taken as additional
        dependencies.
  """

  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self.command_data = []

  def acccept_requirement(self, target):
    if not isinstance(target, Target):
      raise TypeError('can only depend on targets')

  def add(self, *args, **kwargs):
    warnings.warn("Target.add() is deprecated, use "
      "Target.build() instead", DeprecationWarning)
    return self.build(*args, **kwargs)

  def build(self, inputs, outputs, command, each=False):
    """
    Associated the *inputs* with the *outputs* being built by the
    specified *\*commands*. All parameters passed to this function must
    be strings that are automatically and instantly evaluated as macros.

    The data will be appended to :attr:`command_data` in the form of
    a dictionary with the following keys:

    - ``'inputs'``: A list of input files.
    - ``'outputs'``: A list of output files.
    - ``'command'``: A command to produce the output files.

    Args:
      inputs (str): A listing of the input files.
      outputs (str): A listing of the output files.
      command (str): A command to build the outputs from the inputs. The
        special variables `$<` and `$@` represent the input and output.
        The variables `$in` and `$out` will automatically be escaped so
        they will be exported to the ninja rule.
      each (bool): If True, the files will be built each on its own,
        but it expects the caller to use the ``$in`` and ``$out`` macros.
    """

    # Invoke the listeners and allow them to modify the input data.
    # Eg. a plugin could add the header files that are required for
    # the build to the input files.
    data = {
      'inputs': inputs, 'outputs': outputs,
      'command': command, 'auxiliary': [], 'each': each,
    }
    del inputs, outputs, command
    for listener in self.listeners:
      listener(self, 'build', data)

    # Evaluate and split the input files into a list.
    input_files = creator.utils.split(self.unit.eval(data['inputs']))
    input_files = [creator.utils.normpath(f) for f in input_files]

    # Evaluate and split the output files into a list.
    output_files = creator.utils.split(self.unit.eval(data['outputs']))
    output_files = [creator.utils.normpath(f) for f in output_files]

    context = creator.macro.MutableContext()

    if each:
      if len(input_files) != len(output_files):
        raise ValueError('input file count must match output file count')
      for fin, fout in zip(input_files, output_files):
        context['<'] = raw(fin)
        context['@'] = raw(fout)
        command = self.unit.eval(data['command'], context)
        self.command_data.append({
          'inputs': [fin],
          'outputs': [fout],
          'auxiliary': data['auxiliary'],
          'command': command,
        })
    else:
      context['<'] = raw(creator.utils.join(input_files))
      context['@'] = raw(creator.utils.join(output_files))
      command = self.unit.eval(data['command'], context)
      self.command_data.append({
        'inputs': input_files,
        'outputs': output_files,
        'auxiliary': data['auxiliary'],
        'command': command,
      })

  def build_each(self, inputs, outputs, command):
    return self.build(inputs, outputs, command, each=True)

  def export(self, writer):
    """
    Export the target to the ninja file using the *writer*. The target
    and all its dependencies must be set-up.

    Raises:
      RuntimeError: If the target or one of its dependencies is not set-up.
    """

    if not self.is_setup:
      raise RuntimeError('target "{0}" not set-up'.format(self.identifier))

    writer.comment('Target: {0}'.format(self.identifier))

    # The outputs of depending targets must be listed additionally
    # to the actual input files of this target, otherwise ninja can
    # not know the targets depend on each other.
    infiles = set()

    for dep in self.dependencies:
      if not dep.is_setup:
        raise RuntimeError('target "{0}" not set-up'.format(dep.identifier))
      for entry in dep.command_data:
        infiles |= set(map(creator.utils.normpath, entry['outputs']))

    infiles = list(infiles)
    phonies = []

    for index, entry in enumerate(self.command_data):
      rule_name = self.identifier + '_{0:04d}'.format(index)
      rule_name = creator.ninja.ident(rule_name)
      writer.rule(rule_name, entry['command'])

      assert len(entry['outputs']) != 0
      inputs = list(entry['inputs']) + infiles + entry['auxiliary']
      writer.build(entry['outputs'], rule_name, inputs)

      writer.newline()
      phonies.extend(entry['outputs'])

    writer.build(creator.ninja.ident(self.identifier), 'phony', phonies)


class Task(BaseTarget):
  """
  Represents a task-target that is run from Python.
  """

  def __init__(self, task_func, *args, **kwargs):
    if not callable(task_func):
      raise TypeError('task_func must be callable', type(task_func))
    super().__init__(*args, **kwargs)
    self._func = task_func

  @property
  def func(self):
    return self._func


class WorkspaceContext(creator.macro.MutableContext):
  """
  This class implements the :class:`creator.macro.ContextProvider`
  interface for the global macro context of a :class:`Workspace`.
  """

  def __init__(self, workspace):
    super().__init__()
    self._workspace = weakref.ref(workspace)
    self['Platform'] = creator.macro.TextNode(creator.platform.platform_name)
    self['PlatformStandard'] = creator.macro.TextNode(
      creator.platform.platform_standard)
    self['Architecture'] = creator.macro.TextNode(
      creator.platform.architecture)

  @property
  def workspace(self):
    return self._workspace()

  def has_macro(self, name):
    try:
      self.get_macro(name)
    except KeyError:
      return False
    return True

  def get_macro(self, name, default=NotImplemented):
    macro = super().get_macro(name, None)
    if macro is not None:
      return macro
    if not name.startswith('_') and hasattr(creator.macro.Globals, name):
      return getattr(creator.macro.Globals, name)
    if name in os.environ:
      return creator.macro.TextNode(os.environ[name])
    raise KeyError(name)

  def get_namespace(self):
    return ''


class UnitContext(creator.macro.ContextProvider):
  """
  This class implements the :class:`creator.macro.ContextProvider`
  interface for the local macro context of a :class:`Unit`.
  """

  def __init__(self, unit):
    super().__init__()
    self._unit = weakref.ref(unit)
    self['self'] = creator.macro.TextNode(self.unit.identifier)
    self['ProjectPath'] = creator.macro.TextNode(unit.project_path)

  @property
  def unit(self):
    return self._unit()

  @property
  def workspace(self):
    return self._unit().workspace

  def _prepare_name(self, name):
    namespace, varname = creator.utils.parse_var(name)
    if namespace in self.unit.aliases:
      namespace = self.unit.aliases[namespace]
    elif namespace is None:
      namespace = self.unit.identifier
    elif not namespace:
      # Empty namespace specified, the resulting variable
      # should have no namespace identifier in it.
      namespace = None
    return creator.utils.create_var(namespace, varname)

  def __getitem__(self, name):
    name = self._prepare_name(name)
    return self.workspace.context[name]

  def __setitem__(self, name, value):
    if isinstance(value, str):
      value = creator.macro.parse(value, self)
    if not isinstance(value, creator.macro.ExpressionNode):
      raise TypeError('value must be str or ExpressionNode', type(value))
    name = self._prepare_name(name)
    self.workspace.context[name] = value

  def items(self):
    namespace = creator.utils.create_var(self.unit.identifier, '')
    for key, value in self.workspace.context.macros.items():
      if key.startswith(namespace):
        key = key[len(namespace):]
        yield (key, value)

  def update(self, mapping, context_switch=False):
    for key, value in list(mapping.items()):
      if context_switch and isinstance(value, creator.macro.ExpressionNode):
        value = value.copy(self)
      self[key] = value

  def has_macro(self, name):
    if self.workspace.context.has_macro(self._prepare_name(name)):
      return True
    return self.workspace.context.has_macro(name)

  def get_macro(self, name, default=NotImplemented):
    try:
      return self.workspace.context.get_macro(self._prepare_name(name))
    except KeyError:
      try:
        return self.workspace.context.get_macro(name)
      except KeyError:
        pass
    raise KeyError(name)

  def get_namespace(self):
    return self.unit.identifier
