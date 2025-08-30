# meson-python-sources-fixer

This is a minimal linter that checks that all Python source files in a Python
package are listed in a `meson.build` file.

## Installation and Usage

To use it via [pre-commit](https://pre-commit.com/), add the following to your
`.pre-commit-config.yaml`:

```yaml
repos:
-   repo: https://github.com/osandov/meson-python-sources-fixer
    rev: v1.0
    hooks:
    -   id: meson-python-sources-fixer
```

To run it manually:

```console
$ pip install meson-python-sources-fixer
$ cd <project-directory>
$ meson-python-sources-fixer
```

By default, `meson-python-sources-fixer` will automatically create or rewrite
`meson.build` files for Python packages as needed. This behavior can be changed
with the `--check` and `--diff` options.

## Expected Format

`meson-python-sources-fixer` expects a specific layout and format.

### Root `meson.build` File

By default, the root `meson.build` file must have two things:

1. A variable initialized to
   [`import('python').find_installation()`](https://mesonbuild.com/Python-module.html#find_installation).
2. A [`subdir()`](https://mesonbuild.com/Reference-manual_functions.html#subdir)
   call for each top-level Python package in the project.

For example:

```console
$ tree
.
├── meson.build
└── pkg
    ├── foo.py
    ├── __init__.py
    ├── meson.build
    └── nested
        ├── bar.py
        └── __init__.py
$ cat meson.build
...
py = import('python').find_installation()

subdir('pkg')
```

By default, each package is assumed to be installed at the top-level of the
Python installation. E.g., `subdir('pkg')` and `subdir('src/pkg')` would both
be installed as `/usr/lib/pythonX.Y/site-packages/pkg`.

The `import('python').find_installation()` requirement can be overridden with
the `--py-installation` option.

The list of packages and their installation locations can be overridden with
the `--package` option.

`meson-python-sources-fixer` will not modify the root `meson.build` file.

### Package `meson.build` Files

Each top-level Python package should have its own `meson.build` file listing
its sources files with a call to
[`py.install_sources()`](https://mesonbuild.com/Python-module.html#install_sources)
(where `py` is the `python_installation` object defined in the root
`meson.build` file) per subpackage. The sources should be lexicographically
sorted, as should the subpackages. For example:

```console
$ cat pkg/meson.build
py.install_sources(
    '__init__.py',
    'foo.py',
    subdir: 'pkg',
)

py.install_sources(
    'nested/__init__.py',
    'nested/bar.py',
    subdir: 'pkg/nested',
)
```

Note that subpackages (e.g., `pkg/nested`) should not have their own
`meson.build` files. They are represented in the top-level package.

## Limitations

`meson-python-sources-fixer` is relatively simple and has a few limitations:

* It requires an `__init__.py` file in each package.
* It does not require a specific code style, but if a file needs to be updated,
  it will not maintain the existing code style.
* It allows comments inside of and between calls to `py.install_sources()`, but
  if a file needs to be updated, it cannot preserve comments and will therefore
  refuse to update the file.
* It does not understand variables, dynamic lists of sources, loops,
  conditionals, etc.
