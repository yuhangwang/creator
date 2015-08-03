#!/usr/bin/env python3
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

import os
import sys

if hasattr(os.path, 'samefile'):
  samefile = os.path.samefile
else:
  def samefile(fn1, fn2):
    if os.path.isfile(fn1) and os.path.isfile(fn2):
      return os.stat(fn1) == os.stat(fn2)
    else:
      return False

# Remove all paths that contain exactly this file. It would import itself
# instead of the creator module.
basename = os.path.basename(__file__)
for path in sys.path[:]:
  ref_file = os.path.join(path, basename)
  if os.path.isfile(ref_file) and samefile(ref_file, __file__):
    sys.path.remove(path)

import creator.__main__
if __name__ == "__main__":
  sys.exit(creator.__main__.main())
