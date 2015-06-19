# Copyright (C) 2015 Niklas Rosenstein
# All rights reserved.

import creator.macro
import creator.utils
import os
import weakref


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
  """

  def __init__(self):
    super(Workspace, self).__init__()
    self.path = os.getenv('CREATORPATH', '').split(os.pathsep)
    self.path.insert(0, os.path.join(os.path.dirname(__file__), 'builtins'))
    self.context = WorkspaceContextProvider(self)
    self.units = {}

class Unit(object):
  """
  A *Unit* represents a collection of macros and build targets. Each
  unit has a unique identifier and may depend on other units. All units
  in a :class:`Workspace` share the same global macro context and each
  has its own local context as well as a local mapping of unit aliases
  and target declarators.

  Attributes:
    project_path (str): The path of the units project directory.
    identifier (str): The full identifier of the unit.
    workspace (Workspace): The workspace the unit is associated with.
    context (ContextProvider): The local context of the unit.
    aliases (dict of str -> str): A mapping of alias names to fully
      qualified unit identifiers.
  """

  def __init__(self, project_path, identifier, workspace):
    super(Unit, self).__init__()
    self.project_path = project_path
    self.identifier = identifier
    self.workspace = workspace
    self.context = UnitContextProvider(self)
    self.aliases = {}

  def get_identifier(self):
    return self._identifier

  def set_identifier(self, identifier):
    if not isinstance(identifier, str):
      raise TypeError('identifier must be str', type(identifier))
    if not creator.utils.validate_unit_identifier(identifier):
      raise ValueError('invalid unit identifier', identifier)
    self._identifier = identifier

  def get_workspace(self):
    return self._workspace()

  def set_workspace(self, workspace):
    if not isinstance(workspace, Workspace):
      raise TypeError('workspace must be Workspace instance', type(workspace))
    self._workspace = weakref.ref(workspace)

  identifier = property(get_identifier, set_identifier)
  workspace = property(get_workspace, set_workspace)


class WorkspaceContextProvider(creator.macro.MutableContextProvider):
  """
  This class implements the :class:`creator.macro.ContextProvider`
  interface for the global macro context of a :class:`Workspace`.
  """

  def __init__(self, workspace):
    super().__init__()
    self._workspace = weakref.ref(workspace)

  @property
  def workspace(self):
    return self._workspace()

  def has_macro(self, name):
    if super().has_macro(name):
      return True
    return name in os.environ

  def get_macro(self, name, default=NotImplemented):
    # First things first, check if a macro with that name was assigned
    # to this context.
    macro = super().get_macro(name, None)
    if macro is not None:
      return macro
    if hasattr(creator.macro.Globals, name):
      return getattr(creator.macro.Globals, name)
    if name in os.environ:
      return creator.macro.pure_text(os.environ[name])
    raise KeyError(name)


class UnitContextProvider(creator.macro.MutableContextProvider):
  """
  This class implements the :class:`creator.macro.ContextProvider`
  interface for the local macro context of a :class:`Unit`.
  """

  def __init__(self, unit):
    super().__init__()
    self._unit = weakref.ref(unit)
    self['ProjectPath'] = unit.project_path

  @property
  def unit(self):
    return self._unit()

  @property
  def workspace(self):
    return self._unit().workspace

  def has_macro(self, name):
    if super().has_macro(name):
      return True
    return self.unit.workspace.context.has_macro(name)

  def get_macro(self, name, default=NotImplemented):
    macro = super().get_macro(name, None)
    if macro is None:
      macro = self.unit.workspace.context.get_macro(name, None)
    if macro is not None:
      return macro
    elif default is not NotImplemented:
      return default
    else:
      raise KeyError(name)

  def get_namespace(self, name):
    if name in self.unit.aliases:
      identifier = self.unit.aliases[name]
    else:
      identifier = name
    units = self.unit.workspace.units
    if identifier in units:
      return units[identifier].context
    return super().get_namespace(name)
