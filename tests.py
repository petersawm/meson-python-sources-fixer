# Copyright (c) Meta Platforms, Inc. and affiliates.
# SPDX-License-Identifier: MIT

import contextlib
import io
from pathlib import Path
import unittest

import pyfakefs.fake_filesystem_unittest
import regex

from meson_python_sources_fixer import (
    INSTALL_SOURCES_PATTERN,
    CannotFixError,
    find_package_sources,
    find_py_installation,
    find_subdirs,
    fix_package_meson_build,
    update_package_meson_build,
)


class TestFindPyInstallation(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(
            find_py_installation("py = import('python').find_installation()\n"),
            "py",
        )

    def test_has_args(self):
        self.assertEqual(
            find_py_installation(
                "py3 = import('python').find_installation(pure: false)\n"
            ),
            "py3",
        )

    def test_whitespace(self):
        self.assertEqual(
            find_py_installation(
                " python=import( 'python'	)  . find_installation ( )\n"
            ),
            "python",
        )

    def test_newlines(self):
        self.assertEqual(
            find_py_installation(
                """

python = import(
    'python'
).find_installation(
    pure : false
)"""
            ),
            "python",
        )

    def test_comments(self):
        self.assertEqual(
            find_py_installation(
                """\
#py=import('python').find_installation()
py3 = import(#
    'python' # 'asdf'
).find_installation( # foo
    pure :false
)
#py=import('python').find_installation()
"""
            ),
            "py3",
        )

    def test_empty(self):
        self.assertIsNone(find_py_installation(""))

    def test_no_assignment(self):
        self.assertIsNone(find_py_installation("import('python').find_installation()"))


class TestFindSubdirs(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(find_subdirs(""), [])

    def test_none(self):
        self.assertEqual(find_subdirs("message('hello, world')\n"), [])

    def test_one(self):
        self.assertEqual(find_subdirs("subdir('pkg')\n"), ["pkg"])

    def test_commented_out(self):
        self.assertEqual(find_subdirs("# subdir('pkg')\n"), [])

    def test_one_with_misc(self):
        self.assertEqual(
            find_subdirs(
                """\
message('hello')
subdir('pkg')
message('world')
"""
            ),
            ["pkg"],
        )

    def test_whitespace(self):
        self.assertEqual(
            find_subdirs(
                """\
 subdir(  'pkg' )\t
"""
            ),
            ["pkg"],
        )

    def test_newlines(self):
        self.assertEqual(
            find_subdirs(
                """\
subdir(
    'pkg'
)
"""
            ),
            ["pkg"],
        )

    def test_newlines_and_whitespace(self):
        self.assertEqual(
            find_subdirs(
                """\
 subdir (\x20
'pkg'\t
 )\x20
"""
            ),
            ["pkg"],
        )

    def test_comments(self):
        self.assertEqual(
            find_subdirs(
                """\
subdir( # 'foo'
    'pkg1'  # 'bar'
) # subdir('baz')
subdir('pkg2') #
"""
            ),
            ["pkg1", "pkg2"],
        )


class TestFindPackageSources(pyfakefs.fake_filesystem_unittest.TestCase):
    def setUp(self):
        self.setUpPyfakefs()

    def test_one_file(self):
        self.fs.create_file("/pkg/__init__.py")

        self.assertEqual(
            find_package_sources(Path("/pkg"), Path("pkg")),
            {"pkg": {"__init__.py"}},
        )

    def test_multiple_files(self):
        self.fs.create_file("/pkg/__init__.py")
        self.fs.create_file("/pkg/foo.py")
        self.fs.create_file("/pkg/py.typed")
        self.fs.create_file("/pkg/bar.pyi")

        self.assertEqual(
            find_package_sources(Path("/pkg"), Path("pkg")),
            {"pkg": {"__init__.py", "foo.py", "py.typed", "bar.pyi"}},
        )

    def test_not_package(self):
        self.fs.create_file("/pkg/foo.py")
        self.fs.create_file("/pkg/py.typed")

        self.assertEqual(
            find_package_sources(Path("/pkg"), Path("pkg")),
            {},
        )

    def test_nested(self):
        self.fs.create_file("/pkg/__init__.py")
        self.fs.create_file("/pkg/foo.py")

        self.fs.create_file("/pkg/a/__init__.py")
        self.fs.create_file("/pkg/a/bar.py")

        self.fs.create_file("/pkg/b/__init__.py")
        self.fs.create_file("/pkg/b/baz.py")

        self.assertEqual(
            find_package_sources(Path("/pkg"), Path("pkg")),
            {
                "pkg": {"__init__.py", "foo.py"},
                "pkg/a": {"a/__init__.py", "a/bar.py"},
                "pkg/b": {"b/__init__.py", "b/baz.py"},
            },
        )

    def test_nested_not_package(self):
        self.fs.create_file("/pkg/__init__.py")
        self.fs.create_file("/pkg/foo.py")

        self.fs.create_file("/pkg/a/__init__.py")
        self.fs.create_file("/pkg/a/bar.py")

        self.fs.create_file("/pkg/b/baz.py")

        self.fs.create_file("/pkg/b/c/__init__.py")

        self.assertEqual(
            find_package_sources(Path("/pkg"), Path("pkg")),
            {
                "pkg": {"__init__.py", "foo.py"},
                "pkg/a": {"a/__init__.py", "a/bar.py"},
            },
        )


class TestInstallSourcesRegex(unittest.TestCase):
    REGEX = regex.compile(
        INSTALL_SOURCES_PATTERN.format("py"), flags=regex.MULTILINE | regex.VERBOSE
    )

    def assert_matches(self, contents, posargs, kwargs):
        match = self.REGEX.search(contents)
        self.assertTrue(match)

        if match.start() > 0:
            self.assertEqual(contents[match.start() - 1], "\n")
        if match.end() < len(contents):
            self.assertEqual(contents[match.end()], "\n")

        self.assertEqual(match.captures("posarg"), posargs)
        self.assertEqual(
            list(zip(match.captures("keyword"), match.captures("value"))), kwargs
        )

    def test_no_arguments(self):
        self.assert_matches("py.install_sources()\n", [], [])

    def test_no_arguments_no_newline(self):
        self.assert_matches("py.install_sources()", [], [])

    def test_no_arguments_whitespace(self):
        self.assert_matches(
            """
 py  . install_sources (\t
  )
""",
            [],
            [],
        )

    def test_no_arguments_comments(self):
        self.assert_matches(
            """
py.install_sources( # a
  # b
) # c
""",
            [],
            [],
        )

    def test_one_posarg(self):
        for trailing_comma in (",", ""):
            with self.subTest(trailing_comma=bool(trailing_comma)):
                self.assert_matches(
                    f"py.install_sources('a.py'{trailing_comma})\n",
                    ["'a.py'"],
                    [],
                )

    def test_posargs(self):
        for trailing_comma in (",", ""):
            with self.subTest(trailing_comma=bool(trailing_comma)):
                self.assert_matches(
                    f"""\
py.install_sources(
    'a.py',
    'b.py',
    'c.py'{trailing_comma}
)
""",
                    ["'a.py'", "'b.py'", "'c.py'"],
                    [],
                )

    def test_posargs_whitespace_and_comments(self):
        self.assert_matches(
            """\
py.install_sources( # 'd.py',
    'a.py',#
'b.py',\x20
\t'c.py'
)
""",
            ["'a.py'", "'b.py'", "'c.py'"],
            [],
        )

    def test_kwargs(self):
        for trailing_comma in (",", ""):
            with self.subTest(trailing_comma=bool(trailing_comma)):
                self.assert_matches(
                    f"""\
py.install_sources(
    subdir: 'pkg',
    pure: true{trailing_comma}
)
""",
                    [],
                    [("subdir", "'pkg'"), ("pure", "true")],
                )

    def test_posargs_and_kwargs(self):
        for trailing_comma in (",", ""):
            with self.subTest(trailing_comma=bool(trailing_comma)):
                self.assert_matches(
                    f"""\
py.install_sources(
    'a.py',
    'b.py',
    subdir: 'pkg'
    {trailing_comma}
)
""",
                    ["'a.py'", "'b.py'"],
                    [("subdir", "'pkg'")],
                )

    def test_posargs_list(self):
        for sources_trailing_comma in (",", ""):
            for list_trailing_comma in (",", ""):
                with self.subTest(
                    sources_trailing_comma=bool(sources_trailing_comma),
                    list_trailing_comma=bool(list_trailing_comma),
                ):
                    self.assert_matches(
                        f"""\
py.install_sources(
    [
        'a.py',
        'b.py',
        'c.py'{sources_trailing_comma}
    ]{list_trailing_comma}
)
""",
                        ["'a.py'", "'b.py'", "'c.py'"],
                        [],
                    )

    def test_posargs_list_and_kwargs(self):
        for sources_trailing_comma in (",", ""):
            for kwargs_trailing_comma in (",", ""):
                with self.subTest(
                    sources_trailing_comma=bool(sources_trailing_comma),
                    kwargs_trailing_comma=bool(kwargs_trailing_comma),
                ):
                    self.assert_matches(
                        f"""\
py.install_sources(
    [
        'a.py',
        'b.py'{sources_trailing_comma}
    ],
    subdir: 'pkg',
    pure: true {kwargs_trailing_comma}
)
""",
                        ["'a.py'", "'b.py'"],
                        [("subdir", "'pkg'"), ("pure", "true")],
                    )


class TestFixPackageMesonBuild(unittest.TestCase):
    def assert_ok(self, contents, py_installation, expected_sources):
        self.assertEqual(
            fix_package_meson_build(contents, py_installation, expected_sources),
            contents,
        )

    def test_empty(self):
        self.assert_ok("", "py", {})

    def test_non_empty(self):
        for newline in ("\n", ""):
            with self.subTest(newline=bool(newline)):
                self.assert_ok("message('hello, world')" + newline, "py", {})

    def test_up_to_date(self):
        self.assert_ok(
            "py.install_sources('__init__.py', 'foo.py', subdir: 'pkg')\n",
            "py",
            {"pkg": {"__init__.py", "foo.py"}},
        )

    def test_up_to_date_with_kwargs(self):
        self.assert_ok(
            "py.install_sources('__init__.py', 'foo.py', subdir: 'pkg', pure: true)\n",
            "py",
            {"pkg": {"__init__.py", "foo.py"}},
        )

    def test_up_to_date_with_comments(self):
        self.assert_ok(
            """\
py.install_sources( # Before
    # Sources.
    '__init__.py',
    'foo.py',
    # Subdirectory
    subdir: 'pkg',
) # After
""",
            "py",
            {"pkg": {"__init__.py", "foo.py"}},
        )

    def test_multiple_up_to_date(self):
        self.assert_ok(
            """\
message('hello')
py.install_sources('__init__.py', 'foo.py', subdir: 'pkg')
message('world')
py.install_sources(
    [
        'a/__init__.py',
        'a/bar.py',
    ],
    subdir : 'pkg/a',
    pure : true,
)
message('goodbye')
""",
            "py",
            {
                "pkg": {"__init__.py", "foo.py"},
                "pkg/a": {"a/__init__.py", "a/bar.py"},
            },
        )

    def test_up_to_date_with_extra_sources(self):
        self.assert_ok(
            "py.install_sources('__init__.py', 'file.dat', 'foo.py', subdir: 'pkg')\n",
            "py",
            {"pkg": {"__init__.py", "foo.py"}},
        )

    def test_create_in_empty(self):
        self.assertEqual(
            fix_package_meson_build("", "py", {"pkg": {"__init__.py", "foo.py"}}),
            """\
py.install_sources(
    '__init__.py',
    'foo.py',
    subdir: 'pkg',
)
""",
        )

    def test_create_multiple_in_empty(self):
        self.assertEqual(
            fix_package_meson_build(
                "",
                "py",
                {
                    "pkg": {"__init__.py", "foo.py"},
                    "pkg/a": {"a/__init__.py", "a/bar.py"},
                },
            ),
            """\
py.install_sources(
    '__init__.py',
    'foo.py',
    subdir: 'pkg',
)

py.install_sources(
    'a/__init__.py',
    'a/bar.py',
    subdir: 'pkg/a',
)
""",
        )

    def test_create_in_non_empty(self):
        for newline in ("\n", ""):
            with self.subTest(newline=bool(newline)):
                self.assertEqual(
                    fix_package_meson_build(
                        "message('hello, world')" + newline,
                        "py",
                        {"pkg": {"__init__.py", "foo.py"}},
                    ),
                    """\
message('hello, world')

py.install_sources(
    '__init__.py',
    'foo.py',
    subdir: 'pkg',
)
""",
                )

    def test_sort_subdirs(self):
        self.assertEqual(
            fix_package_meson_build(
                """\
py.install_sources(
    [
        'a/__init__.py',
        'a/bar.py',
    ],
    subdir : 'pkg/a',
    pure : true,
)
py.install_sources('__init__.py', 'foo.py', subdir: 'pkg')
""",
                "py",
                {
                    "pkg": {"__init__.py", "foo.py"},
                    "pkg/a": {"a/__init__.py", "a/bar.py"},
                },
            ),
            """\
py.install_sources('__init__.py', 'foo.py', subdir: 'pkg')

py.install_sources(
    [
        'a/__init__.py',
        'a/bar.py',
    ],
    subdir : 'pkg/a',
    pure : true,
)
""",
        )

    def test_update_sources(self):
        self.assertEqual(
            fix_package_meson_build(
                "py.install_sources('__init__.py', subdir: 'pkg/a')\n",
                "py",
                {"pkg/a": {"a/__init__.py", "a/foo.py"}},
            ),
            """\
py.install_sources(
    'a/__init__.py',
    'a/foo.py',
    subdir: 'pkg/a',
)
""",
        )

    def test_sort_subdirs_and_update_sources(self):
        self.assertEqual(
            fix_package_meson_build(
                """\
py.install_sources('__init__.py', subdir: 'pkg/a')
py.install_sources('bar.py', subdir: 'pkg')
""",
                "py",
                {
                    "pkg": {"bar.py", "__init__.py"},
                    "pkg/a": {"a/__init__.py", "a/foo.py"},
                },
            ),
            """\
py.install_sources(
    '__init__.py',
    'bar.py',
    subdir: 'pkg',
)

py.install_sources(
    'a/__init__.py',
    'a/foo.py',
    subdir: 'pkg/a',
)
""",
        )

    def test_sort_sources(self):
        self.assertEqual(
            fix_package_meson_build(
                "py.install_sources('foo.py', '__init__.py', subdir: 'pkg')\n",
                "py",
                {"pkg": {"__init__.py", "foo.py"}},
            ),
            """\
py.install_sources(
    '__init__.py',
    'foo.py',
    subdir: 'pkg',
)
""",
        )

    def test_sort_extra_sources(self):
        self.assertEqual(
            fix_package_meson_build(
                "py.install_sources('foo.py', '__init__.py', 'file.dat', subdir: 'pkg')\n",
                "py",
                {"pkg": {"__init__.py", "foo.py"}},
            ),
            """\
py.install_sources(
    '__init__.py',
    'file.dat',
    'foo.py',
    subdir: 'pkg',
)
""",
        )

    def test_delete(self):
        for newline in ("", "\n"):
            with self.subTest(newline=bool(newline)):
                self.assertEqual(
                    fix_package_meson_build(
                        f"""\
py.install_sources(
    [
        'a/__init__.py',
        'a/bar.py',
    ],
    subdir : 'pkg/a',
    pure : true,
)
py.install_sources('__init__.py', 'foo.py', subdir: 'pkg'){newline}""",
                        "py",
                        {"pkg": {"__init__.py", "foo.py"}},
                    ),
                    "py.install_sources('__init__.py', 'foo.py', subdir: 'pkg')"
                    + newline,
                )

    def test_create_delete_and_sort(self):
        self.assertEqual(
            fix_package_meson_build(
                """\
py.install_sources(
    [
        'a/__init__.py',
        'a/bar.py',
    ],
    subdir : 'pkg/a',
    pure : true,
)
py.install_sources('__init__.py', 'foo.py', subdir: 'pkg')
""",
                "py",
                {
                    "pkg": {"__init__.py", "foo.py"},
                    "pkg/b": {"b/__init__.py", "b/baz.py"},
                },
            ),
            """\
py.install_sources('__init__.py', 'foo.py', subdir: 'pkg')

py.install_sources(
    'b/__init__.py',
    'b/baz.py',
    subdir: 'pkg/b',
)
""",
        )

    def test_delete_only(self):
        for newline_before, newline_after in (
            ("", ""),
            ("", "\n"),
            ("\n", ""),
            ("\n", "\n"),
            ("", "\n\n"),
        ):
            with self.subTest(
                newline_before=newline_before, newline_after=newline_after
            ):
                self.assertEqual(
                    fix_package_meson_build(
                        newline_before
                        + """\
py.install_sources(
    '__init__.py',
    'foo.py',
    subdir: 'pkg',
)"""
                        + newline_after,
                        "py",
                        {},
                    ),
                    "",
                )

    def test_delete_only_extra_newline(self):
        for newline_before, newline_after in (
            ("", "\n\n\n"),
            ("\n", "\n\n"),
        ):
            with self.subTest(
                newline_before=newline_before, newline_after=newline_after
            ):
                self.assertEqual(
                    fix_package_meson_build(
                        f"""\
{newline_before}py.install_sources(
    '__init__.py',
    'foo.py',
    subdir: 'pkg',
){newline_after}""",
                        "py",
                        {},
                    ),
                    "\n",
                )

    def test_delete_multiple(self):
        for separating_newline in ("\n", ""):
            for trailing_newline in ("\n", ""):
                with self.subTest(
                    separating_newline=bool(separating_newline),
                    trailing_newline=bool(trailing_newline),
                ):
                    self.assertEqual(
                        fix_package_meson_build(
                            f"""\
py.install_sources(
    '__init__.py',
    'foo.py',
    subdir: 'pkg',
){separating_newline}
py.install_sources(
    'a/__init__.py',
    'a/foo.py',
    subdir: 'pkg/a',
){trailing_newline}""",
                            "py",
                            {},
                        ),
                        "",
                    )

    def test_delete_before_misc(self):
        for newline in ("", "\n"):
            with self.subTest(newline=bool(newline)):
                self.assertEqual(
                    fix_package_meson_build(
                        f"""\
py.install_sources(
    '__init__.py',
    'foo.py',
    subdir: 'pkg',
){newline}
message('hello, world')
""",
                        "py",
                        {},
                    ),
                    "message('hello, world')\n",
                )

    def test_delete_after_misc(self):
        for newline_before, newline_after in (
            ("", ""),
            ("", "\n"),
            ("\n", ""),
            ("\n", "\n"),
        ):
            with self.subTest(
                newline_before=bool(newline_before), newline_after=bool(newline_after)
            ):
                self.assertEqual(
                    fix_package_meson_build(
                        f"""\
message('hello, world')
{newline_before}py.install_sources(
    '__init__.py',
    'foo.py',
    subdir: 'pkg',
){newline_after}""",
                        "py",
                        {},
                    ),
                    "message('hello, world')\n",
                )

    def test_delete_between_misc(self):
        for newline_before, newline_after in (
            ("", ""),
            ("", "\n"),
            ("\n", ""),
            ("\n", "\n"),
        ):
            with self.subTest(
                newline_before=bool(newline_before), newline_after=bool(newline_after)
            ):
                self.assertEqual(
                    fix_package_meson_build(
                        f"""\
message('hello')
{newline_before}py.install_sources(
    '__init__.py',
    'foo.py',
    subdir: 'pkg',
){newline_after}
message('world')
""",
                        "py",
                        {},
                    ),
                    f"""\
message('hello'){newline_before or newline_after}
message('world')
""",
                )

    def test_delete_first(self):
        self.assertEqual(
            fix_package_meson_build(
                """\
py.install_sources(
    '__init__.py',
    subdir: 'pkg/a',
)

py.install_sources(
    '__init__.py',
    subdir: 'pkg',
)
""",
                "py",
                {"pkg": {"__init__.py"}},
            ),
            """\
py.install_sources(
    '__init__.py',
    subdir: 'pkg',
)
""",
        )

    def test_delete_last(self):
        for newline in ("\n", ""):
            with self.subTest(newline=bool(newline)):
                self.assertEqual(
                    fix_package_meson_build(
                        f"""\
py.install_sources(
    '__init__.py',
    subdir: 'pkg',
)

py.install_sources(
    '__init__.py',
    subdir: 'pkg/a',
){newline}""",
                        "py",
                        {"pkg": {"__init__.py"}},
                    ),
                    f"""\
py.install_sources(
    '__init__.py',
    subdir: 'pkg',
){newline}""",
                )

    def test_delete_middle(self):
        self.assertEqual(
            fix_package_meson_build(
                """\
py.install_sources(
    '__init__.py',
    subdir: 'pkg',
)

py.install_sources(
    '__init__.py',
    subdir: 'pkg/a',
)

py.install_sources(
    '__init__.py',
    subdir: 'pkg/b',
)
""",
                "py",
                {"pkg": {"__init__.py"}, "pkg/b": {"b/__init__.py"}},
            ),
            """\
py.install_sources(
    '__init__.py',
    subdir: 'pkg',
)

py.install_sources(
    'b/__init__.py',
    subdir: 'pkg/b',
)
""",
        )

    def test_insert_before(self):
        self.assertEqual(
            fix_package_meson_build(
                """\
py.install_sources(
    'a/__init__.py',
    subdir: 'pkg/a',
)
""",
                "py",
                {"pkg": {"__init__.py"}, "pkg/a": {"a/__init__.py"}},
            ),
            """\
py.install_sources(
    '__init__.py',
    subdir: 'pkg',
)

py.install_sources(
    'a/__init__.py',
    subdir: 'pkg/a',
)
""",
        )

    def test_insert_after(self):
        for newline in ("\n", ""):
            with self.subTest(newline=newline):
                self.assertEqual(
                    fix_package_meson_build(
                        f"""\
py.install_sources(
    '__init__.py',
    subdir: 'pkg',
){newline}""",
                        "py",
                        {"pkg": {"__init__.py"}, "pkg/a": {"a/__init__.py"}},
                    ),
                    f"""\
py.install_sources(
    '__init__.py',
    subdir: 'pkg',
)

py.install_sources(
    'a/__init__.py',
    subdir: 'pkg/a',
){newline}""",
                )

    def test_insert_after_with_misc(self):
        for newline in ("\n", ""):
            with self.subTest(newline=bool(newline)):
                self.assertEqual(
                    fix_package_meson_build(
                        f"""\
py.install_sources(
    '__init__.py',
    subdir: 'pkg',
){newline}
message('hello, world')
""",
                        "py",
                        {"pkg": {"__init__.py"}, "pkg/a": {"a/__init__.py"}},
                    ),
                    f"""\
py.install_sources(
    '__init__.py',
    subdir: 'pkg',
)

py.install_sources(
    'a/__init__.py',
    subdir: 'pkg/a',
){newline}
message('hello, world')
""",
                )

    def test_insert_middle(self):
        self.assertEqual(
            fix_package_meson_build(
                """\
py.install_sources(
    '__init__.py',
    subdir: 'pkg',
)

py.install_sources(
    'b/__init__.py',
    subdir: 'pkg/b',
)
""",
                "py",
                {
                    "pkg": {"__init__.py"},
                    "pkg/a": {"a/__init__.py"},
                    "pkg/b": {"b/__init__.py"},
                },
            ),
            """\
py.install_sources(
    '__init__.py',
    subdir: 'pkg',
)

py.install_sources(
    'a/__init__.py',
    subdir: 'pkg/a',
)

py.install_sources(
    'b/__init__.py',
    subdir: 'pkg/b',
)
""",
        )

    def test_insert_middle_and_fix_in_place(self):
        self.assertEqual(
            fix_package_meson_build(
                """\
py.install_sources(
    'foo.py',
    subdir: 'pkg',
)

py.install_sources(
    'b/bar.py',
    subdir: 'pkg/b',
)
""",
                "py",
                {
                    "pkg": {"__init__.py"},
                    "pkg/a": {"a/__init__.py"},
                    "pkg/b": {"b/__init__.py"},
                },
            ),
            """\
py.install_sources(
    '__init__.py',
    subdir: 'pkg',
)

py.install_sources(
    'a/__init__.py',
    subdir: 'pkg/a',
)

py.install_sources(
    'b/__init__.py',
    subdir: 'pkg/b',
)
""",
        )

    def test_insert_and_delete(self):
        self.assertEqual(
            fix_package_meson_build(
                """\
py.install_sources(
    '__init__.py',
    subdir: 'pkg',
)

py.install_sources(
    'b/__init__.py',
    subdir: 'pkg/b',
)
""",
                "py",
                {"pkg": {"__init__.py"}, "pkg/a": {"a/__init__.py"}},
            ),
            """\
py.install_sources(
    '__init__.py',
    subdir: 'pkg',
)

py.install_sources(
    'a/__init__.py',
    subdir: 'pkg/a',
)
""",
        )

    def test_insert_and_delete_unsorted(self):
        self.assertEqual(
            fix_package_meson_build(
                """\
py.install_sources(
    'b/__init__.py',
    subdir: 'pkg/b',
)

py.install_sources(
    '__init__.py',
    subdir: 'pkg',
)
""",
                "py",
                {"pkg": {"__init__.py"}, "pkg/a": {"a/__init__.py"}},
            ),
            """\
py.install_sources(
    '__init__.py',
    subdir: 'pkg',
)

py.install_sources(
    'a/__init__.py',
    subdir: 'pkg/a',
)
""",
        )

    def test_cannot_fix_unrecognized_install_sources(self):
        self.assertRaisesRegex(
            CannotFixError,
            "unrecognized",
            fix_package_meson_build,
            """\
sources = ['a.py', 'b.py']
py.install_sources(sources, subdir: 'pkg')
""",
            "py",
            {"pkg": {"a.py", "b.py"}},
        )

    def test_cannot_fix_unrecognized_subdir(self):
        self.assertRaisesRegex(
            CannotFixError,
            "unrecognized.*subdir",
            fix_package_meson_build,
            """\
my_subdir = 'pkg'
py.install_sources('a.py', subdir: my_subdir)
""",
            "py",
            {"pkg": {"a.py", "b.py"}},
        )

    def test_cannot_fix_duplicate_subdir(self):
        self.assertRaisesRegex(
            CannotFixError,
            "duplicate",
            fix_package_meson_build,
            """\
py.install_sources('a.py', subdir: 'pkg')
py.install_sources('b.py', subdir: 'pkg')
""",
            "py",
            {"pkg": {"a.py", "b.py"}},
        )

    def test_cannot_fix_between_install_sources(self):
        self.assertRaisesRegex(
            CannotFixError,
            "non-whitespace between",
            fix_package_meson_build,
            """\
py.install_sources(
    '__init__.py',
    subdir: 'pkg',
)

# Subpackage
py.install_sources(
    'a/__init__.py',
    subdir: 'pkg/a',
)
""",
            "py",
            {
                "pkg": {"__init__.py", "foo.py"},
                "pkg/a": {"a/__init__.py"},
            },
        )

    def test_cannot_fix_comments(self):
        self.assertRaisesRegex(
            CannotFixError,
            "comments",
            fix_package_meson_build,
            """\
py.install_sources( # Before
    # Sources.
    '__init__.py',
    'foo.py',
    # Subdirectory
    subdir: 'pkg',
) # After
""",
            "py",
            {"pkg": {"__init__.py", "bar.py"}},
        )


class TestUpdatePackageMesonBuild(pyfakefs.fake_filesystem_unittest.TestCase):
    def setUp(self):
        self.setUpPyfakefs()

    def assert_ok(self, contents, expected_sources):
        self.fs.create_file("meson.build", contents=contents)

        for mode in ("check", "update"):
            with self.subTest(mode=mode):
                with contextlib.redirect_stderr(io.StringIO()) as stderr:
                    success = update_package_meson_build(
                        Path("meson.build"),
                        "py",
                        expected_sources,
                        mode=mode,
                    )
                    self.assertTrue(success, "stderr:\n" + stderr.getvalue())
                    self.assertFalse(stderr.getvalue())
                self.assertEqual(Path("meson.build").read_text(), contents)

    def test_empty(self):
        self.assert_ok("", {})

    def test_ok(self):
        self.assert_ok(
            """\
py.install_sources(
    '__init__.py',
    'foo.py',
    subdir: 'pkg',
)
""",
            {"pkg": {"__init__.py", "foo.py"}},
        )

    def test_check_error(self):
        contents = """\
py.install_sources(
    '__init__.py',
    subdir: 'pkg',
)
"""
        self.fs.create_file("meson.build", contents=contents)
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            success = update_package_meson_build(
                Path("meson.build"),
                "py",
                {"pkg": {"__init__.py", "foo.py"}},
                mode="check",
            )
            self.assertFalse(success, "stderr:\n" + stderr.getvalue())
            self.assertIn("meson.build is out of date", stderr.getvalue())
            self.assertEqual(Path("meson.build").read_text(), contents)

    def test_check_missing(self):
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            success = update_package_meson_build(
                Path("meson.build"),
                "py",
                {"pkg": {"__init__.py", "foo.py"}},
                mode="check",
            )
            self.assertFalse(success, "stderr:\n" + stderr.getvalue())
            self.assertIn("meson.build does not exist", stderr.getvalue())
            self.assertFalse(Path("meson.build").exists())

    def test_fix(self):
        contents = """\
py.install_sources(
    '__init__.py',
    subdir: 'pkg',
)
"""
        self.fs.create_file("meson.build", contents=contents)
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            success = update_package_meson_build(
                Path("meson.build"),
                "py",
                {"pkg": {"__init__.py", "foo.py"}},
                mode="update",
            )
            self.assertTrue(success, "stderr:\n" + stderr.getvalue())
            self.assertIn("updated meson.build", stderr.getvalue())
            self.assertEqual(
                Path("meson.build").read_text(),
                """\
py.install_sources(
    '__init__.py',
    'foo.py',
    subdir: 'pkg',
)
""",
            )

    def test_diff(self):
        contents = """\
py.install_sources(
    '__init__.py',
    subdir: 'pkg',
)
"""
        self.fs.create_file("meson.build", contents=contents)
        with contextlib.redirect_stdout(
            io.StringIO()
        ) as stdout, contextlib.redirect_stderr(io.StringIO()) as stderr:
            success = update_package_meson_build(
                Path("meson.build"),
                "py",
                {"pkg": {"__init__.py", "foo.py"}},
                mode="diff",
            )
            self.assertFalse(success, "stderr:\n" + stderr.getvalue())
            self.assertIn("+++", stdout.getvalue())
            self.assertIn("meson.build is out of date", stderr.getvalue())
            self.assertEqual(Path("meson.build").read_text(), contents)

    def test_create(self):
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            success = update_package_meson_build(
                Path("meson.build"),
                "py",
                {"pkg": {"__init__.py", "foo.py"}},
                mode="update",
            )
            self.assertTrue(success, "stderr:\n" + stderr.getvalue())
            self.assertIn("created meson.build", stderr.getvalue())
            self.assertEqual(
                Path("meson.build").read_text(),
                """\
py.install_sources(
    '__init__.py',
    'foo.py',
    subdir: 'pkg',
)
""",
            )

    def test_cannot_fix(self):
        contents = """\
sources = ['__init__.py', 'foo.py']
py.install_sources(sources, subdir: 'pkg')
"""
        self.fs.create_file("meson.build", contents=contents)
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            success = update_package_meson_build(
                Path("meson.build"),
                "py",
                {"pkg": {"__init__.py", "foo.py"}},
                mode="update",
            )
            self.assertFalse(success, "stderr:\n" + stderr.getvalue())
            self.assertIn("cannot fix meson.build", stderr.getvalue())
            self.assertEqual(Path("meson.build").read_text(), contents)
