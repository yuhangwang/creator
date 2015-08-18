*Creator* - Meta build system for ninja
=======================================

[![Join the chat at https://gitter.im/creator-build/creator](https://badges.gitter.im/Join%20Chat.svg)](https://gitter.im/creator-build/creator?utm_source=badge&utm_medium=badge&utm_campaign=pr-badge&utm_content=badge)
[![Code Issues](http://www.quantifiedcode.com/api/v1/project/abf468ccc4564f6fb8280e6e646fee3d/badge.svg)](http://www.quantifiedcode.com/app/project/abf468ccc4564f6fb8280e6e646fee3d)

*Creator* is a simple, pure Python meta build system for [ninja][] with focus
on an organised and comprehensible way of specifying the build rules. Unlike
GNU Make, Creator is fully modular with namespaces and global and local
variables. Build definitions are Python scripts we call *Units*.

Check out the [Wiki][] for more information!

> __Important__: Creator is in a very early stage and everything can be
> subject to change! If you want to use Creator, make sure to always use
> the latest version from the *master* branch.

__Get Started__

Check out the [Creator Tutorial][] in the Wiki!

__Features__

- Creator is simple (and pure Python)
- Exports [ninja][] build rules 
- Easily extensible, even from a Unit Python script
- Modular approach to build definitions
- Built-in set of Unit Scripts for platform independency
- Full control over the build process from the command-line
- Mix build definitions with custom tasks (Python functions)

__Install__

To always use the latest version, clone the repository and install
via pip remotely:

```
git clone https://github.com/creator-build/creator.git && cd creator
sudo pip3 install -e .
```

Or to install it correctly do either of the two commands

```
sudo pip3 install .
sudo python3 setup.py install
```

__Example__

In an empty hello_world directory create 'src/main.cpp'

```cpp
~/Desktop/hello_world $ cat src/main.cpp
#include <stdio.h>

int main(void) {
    printf("Hello, World!\n");
    return 0;
}
```

Create a '.creator' file in hello_world:

```python
~/Desktop/hello_world $ cat .creator
# @creator.unit.name = creator.hello_world.cpp

load('platform', 'p')
load('compiler', 'c')

if not defined('BuildDir'):
  define('BuildDir', '$ProjectPath/build')
define('Sources', '$(wildcard $ProjectPath/src/*.cpp)')
define('Objects', '$(p:obj $(move $Sources, $ProjectPath/src, $BuildDir/obj))')
define('Program', '$(p:bin $BuildDir/main)')

@target()
def objects():
  objects.build_each(
    '$Sources', '$Objects', '$c:cpp $c:compileonly $(c:objout $@) $(quote $<)')

@target(objects)
def program():
  program.build('$Objects', '$Program', '$c:cpp $(c:binout $@) $(quotesplit $<)')

@task(program)
def run():
  shell('$(quote $Program)')
```

Use creator to build and run the program

```
niklas ~/Desktop/hello_world_cpp $ creator run
creator: exporting to: build.ninja
creator: running: ninja -f build.ninja creator_hello_world_cpp_objects
[1/1] cl /nologo /c /FoC:\Users\niklas\repos\creator-...klas\repos\creator-build\hello_world.cpp\src\main.cpp
main.cpp
C:\Program Files (x86)\Microsoft Visual Studio 11.0\VC\INCLUDE\xlocale(336) : warning C4530: C++ exception handler used, but unwind semantics are not enabled. Specify /EHsc
creator: running: ninja -f build.ninja creator_hello_world_cpp_program
[1/1] cl /nologo /FeC:\Users\niklas\repos\creator-bui...epos\creator-build\hello_world.cpp\build\obj\main.obj
creator: running task 'creator.hello_world.cpp:run'
Hello, World!
```

See also: [*creator-build/hello_world.cpp*](https://github.com/creator-build/hello_world.cpp)

__Requirements__

- Python 3
- [setuptools][]
- [glob2][]
- [colorama][] (optional)
- [ninja][]

[setuptools]: https://pypi.python.org/pypi/setuptools
[glob2]: https://pypi.python.org/pypi/glob2
[colorama]: https://pypi.python.org/pypi/colorama
[ninja]: https://github.com/martine/ninja
[Wiki]: https://github.com/creator-build/creator/wiki
[Creator Tutorial]: https://github.com/creator-build/creator/wiki/Creator-Tutorial
