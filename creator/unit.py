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
import types
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
    self.G = type('WorkspaceGlobals', (), {})()
    self.G.Platform = creator.platform.platform_name
    self.G.PlatformStandard = creator.platform.platform_standard
    self.G.Architecture = creator.platform.architecture

    # If the current user has a `.creator_profile` file in his
    # home directory, run that file.
    filename = os.path.join(os.path.expanduser('~'), '.creator_profile')
    if os.path.isfile(filename):
      self.run_static_unit(filename)

    # Cache for the metadata that was read from .creator files.
    self._metadata_cache = {}
    self._ident_cache = {}

  def info(self, *args, **kwargs):
    kwargs.setdefault('fg', 'cyan')
    # kwargs.setdefault('file', sys.stderr)
    term_print('==> creator:', *args, **kwargs)

  def warn(self, *args, **kwargs):
    kwargs.setdefault('fg', 'magenta')
    # kwargs.setdefault('file', sys.stderr)
    term_print('==> creator:', *args, **kwargs)

  def error(self, *args, **kwargs):
    exit = kwargs.pop('exit', 1)
    kwargs.setdefault('fg', 'red')
    kwargs.setdefault('attr', ('bright',))
    # kwargs.setdefault('file', sys.stderr)
    term_print('==> creator:', *args, **kwargs)
    if exit is not None:
      sys.exit(1)

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
      raise UnitNotFoundError(identifier)
    return self.units[identifier]

  def find_unit(self, identifier, *, allow_recache=True):
    """
    Searches for the filename of a unit in the search :attr:`path`.

    Args:
      identifier (str): The identifier of the unit to load.
      allow_recache (bool): If set to False, the cache will not be
        re-generated if a unit could not be found.
    Returns:
      str: The path to the unit script.
    Raises:
      UnitNotFoundError: If the unit could not be found.
    """

    filename = self._ident_cache.get(identifier)
    if filename is not None:
      return filename
    if not allow_recache:
      raise UnitNotFoundError(identifier)

    def check_file(path):
      path = creator.utils.normpath(path)
      if path.endswith('.creator') or os.path.basename(path) == 'Creator':
        metadata = self._metadata_cache.get(path)
        if metadata is None:
          metadata = creator.utils.read_metadata(path)
          if 'creator.unit.name' not in metadata:
            self.warn("'{0}' missing @creator.unit.name".format(path))
          self._metadata_cache[path] = metadata

        ident = metadata.get('creator.unit.name')
        if ident is not None:
          self._ident_cache[ident] = path

    # Re-generate the identifier cacheself.
    for dirname in self.path:
      if not os.path.isdir(dirname):
        continue

      for path in creator.utils.abs_listdir(dirname):
        if os.path.isfile(path):
          check_file(path)
        elif os.path.isdir(path):
          for path in creator.utils.abs_listdir(path):
            if os.path.isfile(path):
              check_file(path)

    return self.find_unit(identifier, allow_recache=False)

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

    # Transfer all macros that have been set to the unit before
    # it was loaded to the unit.
    identifier_prefix = identifier + ':'
    for key, value in vars(self.G).items(): # list(self.context.macros.items()):
      if key.startswith(identifier_prefix):
        unit.context[key[len(identifier_prefix):]] = value
        del self.context.macros[key]

    try:
      unit.run_unit_script(filename)
    except Exception:
      del self.units[identifier]
      raise

    return unit

  def get_target(self, identifier, unit=None):
    '''
    Resolves a target identifier and returns the target. If a relative
    target identifier is specified, the *unit* must be specified.
    '''

    namespace, target = creator.utils.parse_var(identifier)
    if not namespace:
      if not unit:
        raise ValueError('relative target identifier but no unit specified')
      namespace = unit.identifier
    return self.get_unit(namespace).get_target(target)

  def setup_targets(self):
    """
    Sets up all targets in the workspace.
    """

    for unit in self.units.values():
      for target in unit.targets.values():
        if not target.is_setup and not target.abstract:
          target.do_setup()

  def all_targets(self):
    """
    Returns:
      list of BaseTarget: A generator yielding all targets
        that are declared in the Workspace, sorted by their
        identifier. Abstract targets will be ignored.
    """

    results = []
    for unit in self.units.values():
      for target in unit.targets.values():
        if not target.abstract:
          results.append(target)
    results.sort(key=lambda x: x.identifier)
    return results


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
    self.G = type('UnitGlobals', (), {})()
    self.scope = vars(self.G)
    self.project_path = project_path
    self.identifier = identifier
    self.workspace = workspace
    self.aliases = {'self': self.identifier}
    self.targets = {}
    self.context = UnitContext(self)
    self._executed_files = set()
    self.scope.update(self._create_scope())

  def _create_scope(self):
    """
    Private. Creates a Python dictionary that acts as the scope for the
    unit script which can be executed with :meth:`run_unit_script`.
    """

    return {
      'unit': self,
      'workspace': self.workspace,
      'G': self.workspace.G,
      'run_task': self.run_task,
      'append': self.append,
      'confirm': self.confirm,
      'define': self.define,
      'defined': self.defined,
      'e': self.eval,
      'eq': self.eq,
      'error': self.error,
      'ne': self.ne,
      'eval': self.eval,
      'exit': sys.exit,
      'extends': self.extends,
      'include': self.run_unit_script,
      'info': self.info,
      'load': self.load,
      'raw': creator.macro.TextNode,
      'shell': self.shell,
      'shell_get': self.shell_get,
      'target': self.target,
      'task': self.task,
      'warn': self.warn,
      'ExitCodeError': creator.utils.ShellCall.ExitCodeError,
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

    filename = creator.utils.normpath(filename)
    if filename in self._executed_files:
      return

    with open(filename) as fp:
      code = compile(fp.read(), filename, 'exec', dont_inherit=True)
    self.scope['__file__'] = filename
    self.scope['__name__'] = '__creator__'
    exec(code, self.scope)
    self._executed_files.add(filename)

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
      self.warn('{0} [Y/n]'.format(text), end=' ')
      response = input().strip().lower()
      if response in ('y', 'yes'):
        return True
      elif response in ('n', 'no'):
        return False
      else:
        print("Please reply Yes or No.", end=' ')

  def define(self, name, text=''):
    self.context[name] = creator.macro.parse_and_resolve(
      name, text, self.context)

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

  def macro(self, text):
    '''
    Creates a expression node from the specified *text* bound
    to this unit. If you assign this to a local variable, make
    sure to prevent cyclic references.
    '''

    return creator.macro.parse(text, self.context)

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

  def extends(self, identifier, inherit_targets=True):
    """
    Loads all the contents of the Unit with the specified *identifier*
    into the scope of this Unit and substitutes the context references
    in the original macros with the context of this unit.

    Args:
      identifier (str): The name of the unit to inherit from.
      inherit_targets (bool): If True, targets will be inherited
        and adjusted to the context of this unit. Abstract targets
        in the source unit will not be abstract in this unit.
    Returns:
      Unit: The Unit matching the *identifier*.
    """

    unit = self.load(identifier)
    for key, value in list(unit.context.items()):
      if key not in ('ProjectPath', 'self'):
        self.context.transition(key, value)

    for key, value in unit.aliases.items():
      self.aliases[key] = value

    if inherit_targets:
      for name, target in unit.targets.items():
        clone = target.copy(self)
        clone.abstract = False
        self.targets[name] = clone

      # Replace abstract dependencies with the non-abstract clones.
      for target in self.targets.values():
        for index, dep in enumerate(target.dependencies):
          for name, ref in unit.target.items():
            if ref.abstract and ref is dep:
              dep = target.dependencies[index] = self.targets[name]

    return unit

  def info(self, *args, **kwargs):
    kwargs['fg'] = kwargs.pop('color', 'cyan')
    items = []
    for arg in args:
      if isinstance(arg, str):
        arg = self.eval(arg)
      items.append(arg)
    self.workspace.info('[{0}]'.format(self.identifier), *items, **kwargs)

  def warn(self, *args, **kwargs):
    kwargs['fg'] = kwargs.pop('color', 'magenta')
    items = []
    for arg in args:
      if isinstance(arg, str):
        arg = self.eval(arg)
      items.append(arg)
    self.workspace.info('[{0}]'.format(self.identifier), *items, **kwargs)

  def error(self, *args, **kwargs):
    kwargs['fg'] = kwargs.pop('color', 'red')
    items = []
    for arg in args:
      if isinstance(arg, str):
        arg = self.eval(arg)
      items.append(arg)
    self.workspace.info('[{0}]'.format(self.identifier), *items, **kwargs)

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
    Runs *command* in the shell and returns a :class:`creator.utils.ShellCall`
    object. *command* is expanded before it is used to spawn a process.

    Returns:
      creator.utils.ShellCall: The object that contains the response data.
    """

    command = self.eval(command)
    if not shell:
      command = shlex.split(command)
    return creator.utils.ShellCall(command, shell=shell, cwd=cwd)

  def target(self, *requirements, abstract=False):
    """
    Wraps a Python function to be treated as a target to declare the
    build definitions. _\*requirements_ must be passed zero or more
    requirements that are to be built before the actual target is.

    Requirements for targets may only be targets, not tasks.

    Arguments:
      \*requirements (str or BaseTarget)
      abstract (bool): Pass True to mark this as an abstract target.
        Abstract targets are ignored when exporting to a Ninja file
        but can be inherited when using :meth:`extends`.

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
      target = Target(self, func.__name__, None, func, abstract=abstract)
      [target.requires(req) for req in requirements]
      self.targets[func.__name__] = target
      return target

    return decorator

  def task(self, *requirements, abstract=False):
    """
    Wraps a Python function as a task which can be invoked by the
    command-line or required by another task. _\*requirements_ must be
    passed zero or more requirements to be built/executed before the
    actual task is executed.

    Requirements may be targets or tasks.

    Arguments:
      \*requirements (str or BaseTarget)
      abstract (bool): Pass True to mark this as an abstract target.
        Abstract targets are ignored when exporting to a Ninja file
        but can be inherited when using :meth:`extends`.

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
      task = Task(func, self, func.__name__, None, abstract=abstract)
      [task.requires(req) for req in requirements]
      self.targets[func.__name__] = task
      return func

    return decorator


class BaseTarget(object):
  '''
  The base class for targets.

  Attributes:
    unit (creator.unit.Unit): The unit this target belongs to.
    name (str): The name of the target.
    dependencies (list of BaseTarget): A list of dependencies that are
      required by this target.
    identifier (str): The identifier of the target, which is the
      units identifier and the targets name concatenated.
    is_setup (bool): True if the target is set-up, False if not.
    on_setup (callable)
    initalizer (callable)
    listeners (list of callable): A list of functions listening to
      certain events of the target. The functions are invoked with
      the three arguments ``(target, event, data)``.

  Listener Events:
    - ``'do_setup'``: Sent when :meth:`do_setup` is called. There is
      no data for this event.
  '''

  def __init__(self, unit, name, initalizer=None, on_setup=None, abstract=False):
    if not isinstance(unit, creator.unit.Unit):
      raise TypeError('unit must be creator.unit.Unit', type(unit))
    if not isinstance(name, str):
      raise TypeError('name must be str', type(name))
    if not creator.utils.validate_identifier(name):
      raise ValueError('name is not a valid identifier', name)
    if initalizer is not None and not callable(initalizer):
      raise TypeError('initalizer must be None or callable')
    if on_setup is not None and not callable(on_setup):
      raise TypeError('on_setup must be None or callable')
    super().__init__()
    self._unit = weakref.ref(unit)
    self._name = name
    self.dependencies = []
    self.listeners = []
    self.is_setup = False
    self.initalizer = initalizer
    self.on_setup = on_setup
    self.abstract = abstract

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
    Adds *target* as a dependency for this target. *target* can be a
    string or target object. If it is a string, it will be resolved
    immediately.

    Note that *target* can only be abstract if *self* is also an
    abstract target. Abstract targets can only depend on abstract
    targets from the same module.

    Args:
      target (str or Target): The target to build before the current.
    """

    if isinstance(target, str):
      target = self.unit.workspace.get_target(target, self.unit)
    if target.abstract:
      if not self.abstract:
        raise ValueError('can not depend on abstract target')
      if self.unit is not target.unit:
        raise ValueError('can not depend on abstract target from different unit')
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
    self.is_setup = True

    for listener in self.listeners:
      listener(self, 'do_setup', None)

    if self.initalizer is not None:
      self.initalizer()
    if self.on_setup is not None:
      self.on_setup()

    return True

  def copy(self, unit):
    '''
    Create a copy of the target. The target will be attached to
    the specified *unit*. The default implementation will create
    a copy of the wrapped function and assign the units scope to
    it.
    '''

    obj = object.__new__(type(self))
    obj._copy_from(self, unit)
    return obj

  def _copy_from(self, target, unit):
    '''
    Private. Fills *self* from the source *target* and using the
    new *unit* instead of the *target*s old unit.
    '''

    if target.on_setup:
      func = target.on_setup
      on_setup = types.FunctionType(func.__code__, unit.scope,
        name=func.__name__, argdefs=func.__defaults__, closure=func.__closure__)
    else:
      on_setup = None

    self._unit = weakref.ref(unit)
    self._name = target.name
    self.dependencies = list(target.dependencies)
    self.listeners = list(target.listeners)
    self.is_setup = False
    self.initalizer = target.initalizer
    self.on_setup = on_setup
    self.abstract = target.abstract
    unit.scope[self._name] = self


class Target(BaseTarget):
  """
  This class represents one or multiple build targets under one common
  identifier. It contains all the information necessary to generate the
  required build commands.

  A target has a set-up phase which is invoked after all units were
  loaded and evaluated. After this phase is complete, the target should
  be completely filled with all data.

  Attributes:
    command_data (list of dict): A list of build commands. Each entry
      is a dictionary with the keys ``'inputs', 'outputs', 'command',
      'auxiliary'``.

  Listener Events:
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

  def _copy_from(self, target, unit):
    super(Target, self)._copy_from(target, unit)
    self.command_data = []


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

  def _copy_from(self, target, unit):
    super(Task, self)._copy_from(target, unit)
    self._func = types.FunctionType(target._func.__code__, unit.scope)


class BaseContext(creator.macro.ContextProvider):
  """
  This class implements the :class:`creator.macro.ContextProvider`
  interface for the local macro context of a :class:`Unit`.
  """

  @property
  def scope(self):
    raise NotImplementedError

  @property
  def workspace(self):
    raise NotImplementedError

  def items(self):
    raise NotImplementedError

  def _prepare_name(self, name):
    return creator.utils.parse_var(name)

  def __setitem__(self, name, value):
    namespace, name = self._prepare_name(name)
    if namespace is None or namespace == self.unit.identifier:
      self.scope[name] = value
    elif self.parent:
      self.parent[creator.utils.create_var(namespace, name)] = value
    else:
      raise RuntimeError('Well, that\'s strange.')

  def transition(self, key, value):
    '''
    Set the variable *key* to the specified *value*. If *value*
    is an ExpressionNode, it will be copied with a context switch
    to this unit context.
    '''

    if isinstance(value, creator.macro.ExpressionNode):
      value = value.copy(self)
    self[key] = value

  def has_macro(self, name):
    namespace, name = self._prepare_name(name)
    if namespace is None and name in self.scope:
      return True
    elif namespace == self.get_namespace():
      return name in self.scope

    parent = self.parent
    if parent:
      full_name = creator.utils.create_var(namespace, name)
      return parent.has_macro(full_name)
    return False

  def get_macro(self, name):
    namespace, name = self._prepare_name(name)
    if namespace == self.get_namespace():
      value = self.scope[name]
      return self._automacro(value)
    elif namespace is None:
      try:
        value = self.scope[name]
        return self._automacro(value)
      except KeyError:
        pass

    parent = self.parent
    if parent:
      full_name = creator.utils.create_var(namespace, name)
      return parent.get_macro(full_name)
    raise KeyError(name)

  def get_namespace(self):
    raise NotImplementedError

  def _automacro(self, value):
    if isinstance(value, creator.macro.ExpressionNode):
      return value
    else:
      return creator.macro.TextNode(str(value))


class UnitContext(BaseContext):

  def __init__(self, unit):
    super().__init__()
    self._unit = weakref.ref(unit)
    self['self'] = creator.macro.TextNode(unit.identifier)
    self['ProjectPath'] = creator.macro.TextNode(unit.project_path)

  def _prepare_name(self, name):
    unit = self._unit()
    namespace, varname = creator.utils.parse_var(name)
    if namespace in unit.aliases:
      namespace = unit.aliases[namespace]
    return namespace, varname

  @property
  def parent(self):
    return self._unit().workspace.context

  @property
  def scope(self):
    return self._unit().scope

  def items(self):
    return vars(self._unit().G).items()

  def get_namespace(self):
    return self._unit().identifier


class WorkspaceContext(BaseContext):

  def __init__(self, workspace):
    super().__init__()
    self._workspace = weakref.ref(workspace)

  @property
  def parent(self):
    return None

  @property
  def scope(self):
    return vars(self._workspace().G)

  def items(self):
    return vars(self._workspace()).items()

  def get_macro(self, name):
    try:
      return super().get_macro(name)
    except KeyError:
      pass
    namespace, name = creator.utils.parse_var(name)
    if not namespace and not name.startswith('_'):
      if hasattr(creator.macro.Globals, name):
        return getattr(creator.macro.Globals, name)
    raise KeyError(name)

  def get_namespace(self):
    return ''
