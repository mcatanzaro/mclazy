#!/usr/bin/python3
# Licensed under the GNU General Public License Version 2
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

# Copyright (C) 2012
#    Richard Hughes <richard@hughsie.com>

""" A simple script that builds GNOME packages for koji """

import glob
import os
import subprocess
import urllib.request
import json
import re
import rpm
import argparse
import fnmatch
import sys

from contextlib import contextmanager

# internal
from branches import BranchesXml
from modules import ModulesXml
from log import print_debug, print_info, print_fail, print_warning

errors = []
updates = []

def log_error(module, message):
    print_fail(message)
    errors.append((module, message))

def run_command(cwd, argv):
    print_debug(f"Running {' '.join(argv)}")
    p = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    output, error = p.communicate()
    if p.returncode != 0:
        print(output)
        print(error)
    return p.returncode

def replace_spec_value(line, replace):
    if line.find(' ') != -1:
        return line.rsplit(' ', 1)[0] + ' ' + replace
    if line.find('\t') != -1:
        return line.rsplit('\t', 1)[0] + '\t' + replace
    return line

def switch_branch_and_reset(pkg_cache, branch_name):
    rc = run_command (pkg_cache, ['git', 'clean', '-dffx'])
    if rc != 0:
        return rc
    rc = run_command (pkg_cache, ['git', 'reset', '--hard', 'HEAD'])
    if rc != 0:
        return rc
    rc = run_command (pkg_cache, ['git', 'checkout', branch_name])
    if rc != 0:
        return rc
    rc = run_command (pkg_cache, ['git', 'reset', '--hard', f"origin/{branch_name}"])
    if rc != 0:
        return rc

    return 0

def sync_to_rawhide_branch(module, pkg_cache, args):
    rc = switch_branch_and_reset (pkg_cache, 'rawhide')
    if rc != 0:
        log_error(module, "switch to 'rawhide' branch")
        return

    # First try a fast-forward merge
    rc = run_command (pkg_cache, ['git', 'merge', '--ff-only', args.fedora_branch])
    if rc != 0:
        print_info("No fast-forward merge possible")
        # ... and if the ff merge fails, fall back to cherry-picking
        rc = run_command (pkg_cache, ['git', 'cherry-pick', args.fedora_branch])
        if rc != 0:
            run_command (pkg_cache, ['git', 'cherry-pick', '--abort'])
            log_error(module, "cherry-pick")
            return

    rc = run_command (pkg_cache, ['git', 'push'])
    if rc != 0:
        log_error(module, "push")
        return

    # Build the package
    if not args.no_build:
        if args.rawhide_side_tag != None:
            rc = run_command (pkg_cache, ['fedpkg', 'build', '--nowait', '--target', args.rawhide_side_tag])
        else:
            rc = run_command (pkg_cache, ['fedpkg', 'build', '--nowait'])
        if rc != 0:
            log_error(module, "build")
            return

def release_series(ver):
    v = ver.split('.')
    if int(v[0]) >= 40:
        return v[0]
    else:
        return f"{v[0]}.{v[1]}"

re_version = re.compile(r'([-.]|\d+|[^-.\d]+)')

# https://docs.python.org/3.0/whatsnew/3.0.html#ordering-comparisons
def cmp(a, b):
    return (a > b) - (a < b)

def version_cmp(a, b):
    """Compares two versions

    Returns
    -1 if a < b
    0  if a == b
    1  if a > b

    Logic from Bugzilla::Install::Util::vers_cmp

    Logic actually carbon copied from ftpadmin
    https://gitlab.gnome.org/Infrastructure/sysadmin-bin/-/blob/78880cd100f6a73acc9dbd8c0dc3cb9a52e6fc23/ftpadmin#L88-141

    And copied once more into mclazy from:
    https://gitlab.gnome.org/GNOME/releng/-/blob/70e85ee60bc5165ec1b5f52d229a61d2676e4f39/tools/smoketesting/downloadsites.py#L187
    """
    assert(a is not None)
    assert(b is not None)

    A = re_version.findall(a.lstrip('0'))
    B = re_version.findall(b.lstrip('0'))

    while A and B:
        a = A.pop(0)
        b = B.pop(0)

        if a == b:
            continue
        elif a == '-':
            return -1
        elif b == '-':
            return 1
        elif a == '.':
            return -1
        elif b == '.':
            return 1
        elif a.isdigit() and b.isdigit():
            c = cmp(a, b) if (a.startswith('0') or b.startswith('0')) else cmp(int(a, 10), int(b, 10))
            if c:
                return c
        elif a.isalpha() and b.isdigit():
            if a == 'alpha' or a == 'beta' or a == 'rc':
                return -1
        elif a.isdigit() and b.isalpha():
            if b == 'alpha' or b == 'beta' or b == 'rc':
                return 1
        else:
            c = cmp(a.upper(), b.upper())
            if c:
                return c

    return cmp(len(A), len(B))

def get_latest_version(versions, max_version=None):
    """Gets the latest version number

    if max_version is specified, gets the latest version number before
    max_version"""
    latest = None
    versions = [v.rstrip(os.path.sep) for v in versions]
    for version in versions:
        if (latest is None or version_cmp(version, latest) > 0) \
           and (max_version is None or version_cmp(version, max_version) < 0):
            latest = version
    return latest

# https://stackoverflow.com/a/5020214
@contextmanager
def create_lock_file(filename):
    file = open(filename, "w")
    file.write(f"{os.getpid()}")
    try:
        yield file
    finally:
        os.unlink(filename)

def main():

    # use the main mirror
    gnome_ftp = 'https://download.gnome.org/sources'
    lockfile = "mclazy.lock"

    # read defaults from command line arguments
    parser = argparse.ArgumentParser(description='Automatically build Fedora packages for a GNOME release')
    parser.add_argument('fedora_branch', metavar='BRANCH', help='The fedora release to target')
    parser.add_argument('--no-simulate', action='store_false', dest='simulate', help='Push the changes this tool makes')
    parser.add_argument('--check-installed', action='store_true', help='Check installed version against built version')
    parser.add_argument('--relax-version-checks', action='store_true', help='Relax checks on the version numbering')
    parser.add_argument('--no-build', action='store_true', help='Do not actually build, e.g. for rawhide')
    parser.add_argument('--mockbuild', default=None, action='store_true', help='Do a local mock build (default when --no-simulate is specified)')
    parser.add_argument('--no-mockbuild', action='store_false', dest='mockbuild', help='Do not do a local mock build (default)')
    parser.add_argument('--no-rawhide-sync', action='store_true', help='Do not push the same changes to git rawhide branch (default whenever appropriate)')
    parser.add_argument('--cache', default="cache", help='The cache of checked out packages')
    parser.add_argument('--modules', default="modules.xml", help='The modules to search')
    parser.add_argument('--branches', default="branches.xml", help='The branches to use')
    parser.add_argument('--buildone', default=None, help='Only build one specific package')
    parser.add_argument('--side-tag', default=None, help='Specify side tag to use for builds on specified branch')
    parser.add_argument('--rawhide-side-tag', default=None, help='Specify side tag to use for builds on Rawhide')
    parser.add_argument('--no-side-tag', action='store_true', default=False, help='Build without any side tag')
    args = parser.parse_args()

    if args.side_tag == None and not args.no_side_tag and not args.simulate:
        print_fail('Must use either --side-tag or --no-side-tag')
        return

    if args.mockbuild is None:
        args.mockbuild = not args.simulate

    # use rpm to check the installed version
    installed_pkgs = {}
    if args.check_installed:
        print_info("Loading rpmdb")
        ts = rpm.TransactionSet()
        mi = ts.dbMatch()
        for h in mi:
            installed_pkgs[h['name']] = h['version']
        print_debug(f"Loaded rpmdb with {len(installed_pkgs)} items")

    # parse the branches.xml file
    branches = BranchesXml(args.branches)

    if args.fedora_branch not in branches:
        print_fail(f"Unknown branch: {args.fedora_branch}")
        return
    args.fedora_branch = branches[args.fedora_branch].name

    if branches[args.fedora_branch].eol:
        print_fail(f"Branch {args.fedora_branch} is EOL")
        return

    if not args.no_rawhide_sync:
        rawhide_rel = branches['rawhide'].gnome_version
        newstable_rel = branches['newstable'].gnome_version
        selected_rel = branches[args.fedora_branch].gnome_version

        if args.fedora_branch == 'rawhide' and newstable_rel == rawhide_rel:
            print_fail(f"rawhide and newstable are currently using the same GNOME release. Run mclazy for newstable instead, or pass --no-rawhide-sync")
            return

        args.no_rawhide_sync = rawhide_rel != selected_rel

    if args.rawhide_side_tag != None:
        if args.fedora_branch == 'rawhide':
            if args.side_tag != None:
                print_warning('Ignoring value of --side-tag because --rawhide-side-tag was specified')
            args.side_tag = args.rawhide_side_tag
            # Note that we don't return here!
        elif args.side_tag == None:
            print_fail('Cannot specify --rawhide-side-tag without --side-tag')
            return
    elif not args.no_rawhide_sync and args.side_tag != None and args.fedora_branch != 'rawhide':
        print_fail('This branch syncs with rawhide and you specified a --side-tag, so you must also specify --rawhide-side-tag')
        return

    # parse the configuration modules.xml file
    modules = []
    data = ModulesXml(args.modules, branches)
    enabled_one = False
    for item in data.items:
        if item.disabled:
            continue
        enabled = False

        # build just this
        if args.buildone == item.name:
            enabled = True
            enabled_one = True

        # build everything
        if args.buildone == None:
            enabled = True
        if enabled:
            modules.append((item.name, item.pkgname, item.version_limit))

    if args.buildone and enabled_one is False:
        print_fail(f"Invalid module name {args.buildone} passed to --buildone")
        return

    # create the cache directory if it's not already existing
    if not os.path.isdir(args.cache):
        os.mkdir(args.cache)

    # loop these
    for module, pkg, release_version in modules:
        print_info(f"Loading {module}")
        print_debug(f"Package name: {pkg}")
        print_debug(f"Version limit: {release_version[args.fedora_branch]}")

        max_version = release_version[args.fedora_branch]
        if max_version == "ignore":
            print_debug("Skipping because configuration says to ignore this package")
            continue

        # ensure we've not locked this build in another instance
        lock_filename = f"{args.cache}/{pkg}-{lockfile}"
        if os.path.exists(lock_filename):
            # check this process is still running
            is_still_running = False
            with open(lock_filename, 'r') as f:
                try:
                    pid = int(f.read())
                    if os.path.isdir(f"/proc/{pid}"):
                        is_still_running = True
                except ValueError as e:
                    # pid in file was not an integer
                    pass

            if is_still_running:
                print_info(f"Ignoring as another process (PID {pid}) has this")
                continue
            else:
                log_error(module, f"Process with PID {pid} locked but did not release")
                log_error(module, "(This means a previous instance of mclazy died uncleanly)")

        # create lockfile
        with create_lock_file(lock_filename):
            pkg_cache = os.path.join(args.cache, pkg)

            # ensure package is checked out
            if not os.path.isdir(f"{args.cache}/{pkg}"):
                rc = run_command(args.cache, ["fedpkg", "co", pkg])
                if rc != 0:
                    log_error(module, f"Checkout {pkg}")
                    continue
            else:
                rc = run_command (pkg_cache, ['git', 'fetch'])
                if rc != 0:
                    log_error(module, f"Update repo {pkg}")
                    continue

            rc = switch_branch_and_reset (pkg_cache, args.fedora_branch)
            if rc != 0:
                log_error(module, "Switch branch")
                continue

            # get the current version
            version = 0
            version_dot = 0
            spec_filename = f"{args.cache}/{pkg}/{pkg}.spec"
            if not os.path.exists(spec_filename):
                log_error(module, "No spec file")
                continue

            # open spec file
            try:
                spec = rpm.spec(spec_filename)
                version = spec.sourceHeader["version"]
                version_dot = re.sub('([0-9]+)~(alpha|beta|rc)', r'\1.\2', version)
            except ValueError as e:
                log_error(module, "Can't parse spec file")
                continue
            print_debug(f"Current version is {version}")

            # check for newer version on GNOME.org
            success = False
            for i in range (1, 20):
                try:
                    urllib.request.urlretrieve (f"{gnome_ftp}/{module}/cache.json", f"{args.cache}/{pkg}/cache.json")
                    success = True
                    break
                except IOError as e:
                    log_error(module, f"Failed to get JSON on try {i}: {e}")
            if not success:
                continue

            local_json_file = f"{args.cache}/{pkg}/cache.json"
            with open(local_json_file, 'r') as f:

                # the format of the json file is as follows:
                # j[0] = some kind of version number?
                # j[1] = the files keyed for each release, e.g.
                #        { 'pkgname' : {'2.91.1' : {u'tar.gz': u'2.91/gpm-2.91.1.tar.gz'} } }
                # j[2] = array of remote versions, e.g.
                #        { 'pkgname' : {  '3.3.92', '3.4.0' }
                # j[3] = the LATEST-IS files
                try:
                    j = json.loads(f.read())
                except Exception as e:
                    log_error(module, f"Failed to read JSON at {local_json_file}: {str(e)}")
                    continue

                # find the newest version
                newest_remote_version = get_latest_version(j[2][module], max_version)
            if newest_remote_version is None:
                log_error(module, f"No remote versions less than the version limit {max_version}")
                continue


            # is this newer than the rpm spec file version
            newest_remote_version_tilde = re.sub('([0-9]+).(alpha|beta|rc)', r'\1~\2', newest_remote_version)
            rc = rpm.labelCompare((None, newest_remote_version_tilde, None), (None, version, None))
            new_version = None
            new_version_tilde = None
            if rc > 0:
                new_version = newest_remote_version
                new_version_tilde = newest_remote_version_tilde

            # check the installed version
            if args.check_installed:
                if pkg in installed_pkgs:
                    installed_ver = installed_pkgs[pkg]
                    if installed_ver == newest_remote_version:
                        print_debug("installed version is up to date")
                    else:
                        print_debug(f"installed version is {installed_ver}")
                        rc = rpm.labelCompare((None, installed_ver, None), (None, newest_remote_version_tilde, None))
                        if rc > 0:
                            log_error(module, "installed version is newer than gnome branch version")
                            log_error(module, "check modules.xml is looking at the correct branch")
                            continue

            # nothing to do
            if new_version == None:
                print_debug("No updates available")
                continue

            # don't do major updates unless requested
            if new_version:
                current_version = version_dot
                current_major_version = current_version.split('.')[0]
                new_major_version = new_version.split('.')[0]
                if current_major_version != new_major_version and int(new_major_version) < 40:
                    if args.relax_version_checks:
                        print_debug(f"Updating from {current_version} to {new_version} is allowed due to --relax-version-checks")
                    else:
                        log_error(module, f"Cannot update from {current_version} to {new_version} without --relax-version-checks")
                        continue

            # we need to update the package
            if new_version:
                print_debug(f"Need to update from {version} to {new_version_tilde}")

            # download the tarball if it doesn't exist
            if new_version:
                tarball = None
                try:
                    tarball = j[1][module][new_version]['tar.xz']
                except KeyError:
                    try:
                        tarball = j[1][module][new_version]['tar.gz']
                    except KeyError:
                        log_error(module, f"Cannot find tarball for {module}")
                        continue
                dest_tarball = tarball.split('/')[1]
                if os.path.exists(f"{pkg}/{dest_tarball}"):
                    print_debug(f"Source {dest_tarball} already exists")
                else:
                    tarball_url = f"{gnome_ftp}/{module}/{tarball}"
                    print_debug(f"Download {tarball_url}")
                    try:
                        urllib.request.urlretrieve (tarball_url, f"{args.cache}/{pkg}/{dest_tarball}")
                    except IOError as e:
                        log_error(module, f"Failed to get tarball: {e}")
                        continue
                    if not args.simulate:
                        # add the new source
                        rc = run_command (pkg_cache, ['fedpkg', 'new-sources', dest_tarball])
                        if rc != 0:
                            log_error(module, f"Failed to upload new sources for {pkg}")
                            continue

            # prep the spec file for rpmdev-bumpspec
            if new_version:
                with open(spec_filename, 'r') as f:
                    with open(spec_filename+".tmp", "w") as tmp_spec:
                        for line in f:
                            if line.startswith('Version:'):
                                line = replace_spec_value(line, new_version_tilde + '\n')
                            elif line.startswith('Release:') and 'autorelease' not in line:
                                line = replace_spec_value(line, '0%{?dist}\n')
                            elif line.startswith(('Source:', 'Source0:')):
                                line = re.sub(f"/{release_series(version_dot)}/",
                                              f"/{release_series(new_version)}/",
                                              line)
                            tmp_spec.write(line)
                os.rename(f"{spec_filename}.tmp", spec_filename)

            # bump the spec file
            comment = "Update to " + new_version
            cmd = ['rpmdev-bumpspec', "--legacy-datestamp", f"--comment={comment}", f"{pkg}.spec"]
            run_command (pkg_cache, cmd)

            # run prep, and make sure patches still apply
            rc = run_command (pkg_cache, ['fedpkg', 'prep'])
            if rc != 0:
                log_error(module, f"package {pkg} failed prep (do the patches not apply?)")
                continue

            if args.mockbuild:
                rc = run_command (pkg_cache, ['fedpkg', 'mockbuild'])
                if rc != 0:
                    log_error(module, f"package {pkg} failed mock test build")
                    continue

                resultsglob = os.path.join(pkg_cache, f"results_{pkg}/*/*/*.rpm")
                if not glob.glob(resultsglob):
                    log_error(module, f"package {pkg} failed mock test build: no results")
                    continue

            # commit the changes
            rc = run_command (pkg_cache, ['git', 'commit', '-a', f"--message={comment}"])
            if rc != 0:
                log_error(module, "commit")
                continue

            # push the changes
            if args.simulate:
                print_debug("Not pushing as simulating")
                updates.append((module, version, new_version_tilde))
                continue

            rc = run_command (pkg_cache, ['git', 'push'])
            if rc != 0:
                log_error(module, "push")
                continue

            # Try to push the same change to rawhide branch
            if args.rawhide_sync and args.fedora_branch != 'rawhide':
                sync_to_rawhide_branch (module, pkg_cache, args)
                run_command (pkg_cache, ['git', 'checkout', args.fedora_branch])

            # build package
            if not args.no_build:
                pkg_release_tag = branches[args.fedora_branch].release_tag
                if new_version_tilde:
                    print_info(f"Building {pkg}-{new_version_tilde}-1.{pkg_release_tag}")
                else:
                    print_info(f"Building {pkg}-{version}-1.{pkg_release_tag}")
                if args.side_tag != None:
                    rc = run_command (pkg_cache, ['fedpkg', 'build', '--nowait', '--target', args.side_tag])
                else:
                    rc = run_command (pkg_cache, ['fedpkg', 'build', '--nowait'])
                if rc != 0:
                    log_error(module, "Build")
                    continue

            # success!
            updates.append((module, version, new_version_tilde))
            print_info("Done")

    if (len(updates) == 0):
        print_info("Completed processing without updating any modules")
    else:
        print_info("Summary of updated modules:")
        for (module, oldver, newver) in updates:
            print_info(f"{module}: {oldver} -> {newver}")
        if args.simulate:
            print_info("(This is a simulation so nothing was actually updated. Pass --no-simulate to apply this update)")
        else:
            print_info("You must check koji yourself to look for build failures.")

    if (len(errors) == 0):
        print_info("Completed processing without any errors")
    else:
        print_info("Summary of errors:")
        for (module, message) in errors:
            print_fail(f"{module}: {message}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(1)
