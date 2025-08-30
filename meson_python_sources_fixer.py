#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# SPDX-License-Identifier: MIT

import argparse
import ast
import difflib
from pathlib import Path
import sys
from typing import Dict, Iterable, Literal, Mapping, Optional, Set

import regex

if sys.version_info >= (3, 10):
    from itertools import pairwise  # novermin
else:

    def pairwise(iterable):
        iterator = iter(iterable)
        a = next(iterator, None)

        for b in iterator:
            yield a, b
            a = b


def print_bold(s: str, *, color: bool) -> None:
    if color:
        sys.stderr.write("\033[1m" + s + "\033[m\n")
    else:
        print(s, file=sys.stderr)


def print_error(s: str, *, color: bool) -> None:
    if color:
        sys.stderr.write("\033[31merror:\033[m " + s + "\n")
    else:
        print(s, file=sys.stderr)


def print_diff(
    old_contents: str, new_contents: str, old_path: str, new_path: str, *, color: bool
) -> None:
    diff_lines = difflib.unified_diff(
        old_contents.splitlines(keepends=True),
        new_contents.splitlines(keepends=True),
        old_path,
        new_path,
    )

    if not color:
        sys.stdout.writelines(diff_lines)
        return

    control = True
    for line in diff_lines:
        if line.startswith("@"):
            control = False
            code = "36"  # Cyan
        elif control:
            code = "1"  # Bold
        elif line.startswith("+"):
            code = "32"  # Green
        elif line.startswith("-"):
            code = "31"  # Red
        else:
            code = ""

        if code:
            parts = ["\033[", code, "m"]
            if line.endswith("\n"):
                parts.append(line[:-1])
                parts.append("\033[m\n")
            else:
                parts.append(line)
                parts.append("\033[m")
            sys.stdout.write("".join(parts))
        else:
            sys.stdout.write(line)


STRING_PATTERN = r"'(?:\\.|[^'])*'"
COMMENT_PATTERN = r"[#].*"
NON_NEWLINE_WHITESPACE = r"[^\S\n]*"
WHITESPACE_OR_COMMENT_CAPTURE = r"(?:\s|(?&comment))*"
WHITESPACE_OR_COMMENT_EOL_CAPTURE = r"[^\S\n]*(?&comment)?$"
WHITESPACE_OR_COMMENT = WHITESPACE_OR_COMMENT_CAPTURE.replace(
    "(?&comment)", COMMENT_PATTERN
)
WHITESPACE_OR_COMMENT_EOL = WHITESPACE_OR_COMMENT_EOL_CAPTURE.replace(
    "(?&comment)", "(?:" + COMMENT_PATTERN + ")"
)


PY_INSTALLATION_PATTERN = rf"""
^
{NON_NEWLINE_WHITESPACE}
(\w+)
{NON_NEWLINE_WHITESPACE}
=
{NON_NEWLINE_WHITESPACE}
import
{NON_NEWLINE_WHITESPACE}
\(
{WHITESPACE_OR_COMMENT}
'python'
{WHITESPACE_OR_COMMENT}
\)
{NON_NEWLINE_WHITESPACE}
\.
{NON_NEWLINE_WHITESPACE}
find_installation
{NON_NEWLINE_WHITESPACE}
\(
"""


def find_py_installation(contents: str) -> Optional[str]:
    match = regex.search(
        PY_INSTALLATION_PATTERN, contents, flags=regex.MULTILINE | regex.VERBOSE
    )
    return match.group(1) if match else None
    if not match:
        raise LookupError("import('python').find_installation() not found")
    return match.group(1)


SUBDIR_PATTERN = rf"""
^
{NON_NEWLINE_WHITESPACE}
subdir
{NON_NEWLINE_WHITESPACE}
\(
{WHITESPACE_OR_COMMENT}
({STRING_PATTERN})
{WHITESPACE_OR_COMMENT}
\)
{WHITESPACE_OR_COMMENT_EOL}
"""


def find_subdirs(contents: str) -> Iterable[str]:
    return [
        ast.literal_eval(s)
        for s in regex.findall(
            SUBDIR_PATTERN, contents, flags=regex.MULTILINE | regex.VERBOSE
        )
    ]


def is_python_source(name: str) -> bool:
    return name.endswith(".py") or name.endswith(".pyi") or name == "py.typed"


def find_package_sources(source_path: Path, install_path: Path) -> Dict[str, Set[str]]:
    sources = {}
    if (source_path / "__init__.py").exists():
        for root, dirs, files in source_path.walk():
            dirs[:] = [dir for dir in dirs if (root / dir / "__init__.py").exists()]

            relative = root.relative_to(source_path)
            sources[str(install_path / relative)] = {
                str(relative / file) for file in files if is_python_source(file)
            }
    return sources


INSTALL_SOURCES_PATTERN = rf"""
    (?(DEFINE)
        (?<posarg>{STRING_PATTERN})
        (?<list>
            \[
            {WHITESPACE_OR_COMMENT_CAPTURE}
            (?:
                (?&posarg)
                {WHITESPACE_OR_COMMENT_CAPTURE}
                ,
                {WHITESPACE_OR_COMMENT_CAPTURE}
            )*
            (?:
                (?&posarg)
                {WHITESPACE_OR_COMMENT_CAPTURE}
            )?
            \]
        )
        (?<posarg_or_list>(?&posarg)|(?&list))
        (?<kwarg>
            (?<keyword>\w+)
            {WHITESPACE_OR_COMMENT_CAPTURE}
            :
            {WHITESPACE_OR_COMMENT_CAPTURE}
            (?<value>{STRING_PATTERN}|\w+)
        )
        (?<comment>{COMMENT_PATTERN})
    )
    ^
    {NON_NEWLINE_WHITESPACE}
    {{}}
    {NON_NEWLINE_WHITESPACE}
    \.
    {NON_NEWLINE_WHITESPACE}
    install_sources
    {NON_NEWLINE_WHITESPACE}
    \(
    (?:
        {WHITESPACE_OR_COMMENT_CAPTURE}
        (?:
            (?&posarg_or_list)
            {WHITESPACE_OR_COMMENT_CAPTURE}
            ,
            {WHITESPACE_OR_COMMENT_CAPTURE}
        )*
        (?:
            (?:
                (?&posarg_or_list)
                {WHITESPACE_OR_COMMENT_CAPTURE}
            )?
            |
            (?:
                (?&kwarg)
                {WHITESPACE_OR_COMMENT_CAPTURE}
                ,
                {WHITESPACE_OR_COMMENT_CAPTURE}
            )*
            (?:
                (?&kwarg)
                {WHITESPACE_OR_COMMENT_CAPTURE}
            )?
        )
        \)
        {WHITESPACE_OR_COMMENT_EOL_CAPTURE}
        |
        (?<unrecognized>.|$)
    )
"""


class CannotFixError(Exception):
    pass


def fix_package_meson_build(
    contents: str,
    py_installation: str,
    package_sources: Mapping[str, Set[str]],
) -> str:
    matches = {}
    found_install_sources = {}
    for match in regex.finditer(
        INSTALL_SOURCES_PATTERN.format(regex.escape(py_installation)),
        contents,
        flags=regex.MULTILINE | regex.VERBOSE,
    ):
        if match.group("unrecognized"):
            raise CannotFixError(
                f"unrecognized {py_installation}.install_sources() syntax"
            )

        kwargs = dict(zip(match.captures("keyword"), match.captures("value")))
        try:
            subdir_arg = kwargs["subdir"]
        except KeyError:
            subdir = "."
        else:
            if not subdir_arg.startswith("'"):
                raise CannotFixError(
                    f"unrecognized {py_installation}.install_sources() subdir syntax"
                )
            subdir = ast.literal_eval(subdir_arg)

        if subdir in matches:
            raise CannotFixError(
                f"duplicate {py_installation}.install_sources(..., subdir: {subdir!r})"
            )

        matches[subdir] = match
        found_install_sources[subdir] = (
            [ast.literal_eval(arg) for arg in match.captures("posarg")],
            kwargs,
        )

    expected_install_sources = []
    for subdir, sources in package_sources.items():
        try:
            found_sources, kwargs = found_install_sources[subdir]
        except KeyError:
            kwargs = {"subdir": repr(subdir)}
        else:
            sources = sources | {
                source for source in found_sources if not is_python_source(source)
            }
        expected_install_sources.append((subdir, (sorted(sources), kwargs)))
    expected_install_sources.sort()

    if list(found_install_sources.items()) == expected_install_sources:
        return contents

    for match1, match2 in pairwise(matches.values()):
        if regex.search(r"\S", contents, pos=match1.end(), endpos=match2.start()):
            raise CannotFixError(
                f"non-whitespace between {py_installation}.install_sources() calls"
            )

    if matches:
        matches_start = next(iter(matches.values())).start()
        matches_end = next(reversed(matches.values())).end()

        if not expected_install_sources:
            if matches_end < len(contents):
                assert contents[matches_end] == "\n"
                matches_end += 1

            blank_before = matches_start < 2 or contents[matches_start - 2] == "\n"
            blank_after = matches_end == len(contents) or contents[matches_end] == "\n"
            if blank_before and blank_after:
                if matches_end < len(contents):
                    matches_end += 1
                elif matches_start > 0:
                    matches_start -= 1

        parts = [contents[:matches_start]]
    else:
        parts = [contents]
        if contents:
            if not contents.endswith("\n"):
                parts.append("\n\n")
            elif not contents.endswith("\n\n"):
                parts.append("\n")

    for i, (subdir, (expected_sources, kwargs)) in enumerate(expected_install_sources):
        if found_install_sources.get(subdir) == (expected_sources, kwargs):
            parts.append(matches[subdir].group(0))
        else:
            if subdir in matches and matches[subdir].captures("comment"):
                raise CannotFixError(
                    f"{py_installation}.install_sources() call contains comments"
                )
            parts.append(py_installation)
            parts.append(".install_sources(\n")
            for source in expected_sources:
                parts.append(f"    {source!r},\n")
            for keyword, value in kwargs.items():
                parts.append(f"    {keyword}: {value},\n")
            parts.append(")")

        if i < len(expected_install_sources) - 1:
            parts.append("\n\n")

    if matches:
        parts.append(contents[matches_end:])
    else:
        parts.append("\n")

    return "".join(parts)


def update_package_meson_build(
    meson_build_path: Path,
    py_installation: str,
    package_sources: Mapping[str, Set[str]],
    *,
    mode: Literal["update", "check", "diff"],
    color_stdout: bool = False,
    color_stderr: bool = False,
) -> bool:
    try:
        contents = meson_build_path.read_text()
        exists = True
    except FileNotFoundError:
        contents = ""
        exists = False

    orig_contents = contents

    try:
        contents = fix_package_meson_build(contents, py_installation, package_sources)
    except CannotFixError as e:
        print_error(f"cannot fix {meson_build_path}: {e}", color=color_stderr)
        return False

    if contents != orig_contents:
        if mode == "update":
            meson_build_path.write_text(contents)
            if exists:
                print_bold(f"updated {meson_build_path}", color=color_stderr)
            else:
                print_bold(f"created {meson_build_path}", color=color_stderr)
        else:
            if mode == "diff":
                print_diff(
                    orig_contents,
                    contents,
                    str(meson_build_path) if exists else "/dev/null",
                    str(meson_build_path),
                    color=color_stdout,
                )
                sys.stdout.flush()
            if exists:
                print_bold(f"{meson_build_path} is out of date", color=color_stderr)
            else:
                print_bold(f"{meson_build_path} does not exist", color=color_stderr)
            return False

    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="rewrite meson.build files in the current project to include all Python package sources"
    )
    parser.add_argument(
        "--py-installation",
        metavar="NAME",
        help="name of python_installation object (default: find in meson.build)",
    )
    parser.add_argument(
        "--package",
        nargs=2,
        metavar=("SOURCE_PATH", "INSTALL_PATH"),
        type=Path,
        action="append",
        help="paths of packages and where they should be installed (default: find in meson.build)",
    )
    parser.add_argument(
        "--check",
        dest="mode",
        action="store_const",
        const="check",
        default="update",
        help="don't rewrite any files, just exit with status 1 if any files need to be updated",
    )
    parser.add_argument(
        "--diff",
        dest="mode",
        action="store_const",
        const="diff",
        help="don't rewrite any files, just exit with status 1 and print a diff to standard output if any files need to be updated",
    )
    parser.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default="auto",
        help="colorize output",
    )
    args = parser.parse_args()

    if args.color == "auto":
        color_stdout = sys.stdout.isatty()
        color_stderr = sys.stderr.isatty()
    else:
        color_stdout = color_stderr = args.color == "always"

    meson_build_path = Path("meson.build")
    if args.py_installation is None or not args.package:
        meson_build_contents = meson_build_path.read_text()

    if args.py_installation is None:
        args.py_installation = find_py_installation(meson_build_contents)
        if args.py_installation is None:
            print_error(
                f"{meson_build_path}: import('python').find_installation() not found. Try --py-installation.",
                color=color_stderr,
            )
            return 1

    if args.package:
        packages = []
        for source_path, install_path in args.package:
            package_sources = find_package_sources(source_path, install_path)
            if not package_sources:
                print_error(f"{source_path}: not a Python package", color=color_stderr)
                return 1
            packages.append((source_path, package_sources))
    else:
        packages = []
        for subdir in find_subdirs(meson_build_contents):
            subdir_path = Path(subdir)
            source_path = subdir_path
            package_sources = find_package_sources(source_path, Path(subdir_path.name))
            if package_sources:
                packages.append((source_path, package_sources))
        if not packages:
            print_error(
                f"{meson_build_path}: no Python package subdir() calls found. Try --package.",
                color=color_stderr,
            )
            return 1

    exit_status = 0
    for source_path, package_sources in packages:
        if not update_package_meson_build(
            source_path / "meson.build",
            args.py_installation,
            package_sources,
            mode=args.mode,
            color_stdout=color_stdout,
            color_stderr=color_stderr,
        ):
            exit_status = 1
    return exit_status


if __name__ == "__main__":
    sys.exit(main())
