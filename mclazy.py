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

from contextlib import contextmanager

# internal
from modules import ModulesXml
from log import print_debug, print_info, print_fail

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
    parser.add_argument('--fedora-branch', default="rawhide", help='The fedora release to target (default: rawhide)')
    parser.add_argument('--simulate', action='store_true', help='Do not push any changes')
    parser.add_argument('--no-simulate', action='store_true', help='Push changes')
    parser.add_argument('--check-installed', action='store_true', help='Check installed version against built version')
    parser.add_argument('--relax-version-checks', action='store_true', help='Relax checks on the version numbering')
    parser.add_argument('--no-build', action='store_true', help='Do not actually build, e.g. for rawhide')
    parser.add_argument('--no-mockbuild', action='store_true', help='Do not do a local mock build')
    parser.add_argument('--no-rawhide-sync', action='store_true', help='Do not push the same changes to git rawhide branch')
    parser.add_argument('--cache', default="cache", help='The cache of checked out packages')
    parser.add_argument('--modules', default="modules.xml", help='The modules to search')
    parser.add_argument('--buildone', default=None, help='Only build one specific package')
    parser.add_argument('--buildroot', default=None, help='Use a custom buildroot, e.g. f18-gnome')
    args = parser.parse_args()

    if (args.simulate and args.no_simulate):
        print_fail('Cannot use both --simulate and --no-simulate')
        return
    if (not args.simulate and not args.no_simulate):
        print_fail('Must use either --simulate or --no-simulate')
        return

    # use rpm to check the installed version
    installed_pkgs = {}
    if args.check_installed:
        print_info("Loading rpmdb")
        ts = rpm.TransactionSet()
        mi = ts.dbMatch()
        for h in mi:
            installed_pkgs[h['name']] = h['version']
        print_debug(f"Loaded rpmdb with {len(installed_pkgs)} items")

    # parse the configuration file
    modules = []
    data = ModulesXml(args.modules)
    for item in data.items:
        if item.disabled:
            continue
        enabled = False

        # build just this
        if args.buildone == item.name:
            enabled = True

        # build everything
        if args.buildone == None:
            enabled = True
        if enabled:
            modules.append((item.name, item.pkgname, item.release_glob))

    # create the cache directory if it's not already existing
    if not os.path.isdir(args.cache):
        os.mkdir(args.cache)

    # loop these
    for module, pkg, release_version in modules:
        print_info(f"Loading {module}")
        print_debug(f"Package name: {pkg}")
        print_debug(f"Version glob: {release_version[args.fedora_branch]}")

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

            default_gnome_branch = release_version[args.fedora_branch]
            gnome_branch = default_gnome_branch
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
                newest_remote_version = '0'
                newest_remote_version_tilde = '0'
                for remote_ver in j[2][module]:
                    remote_ver_tilde = re.sub('([0-9]+).(alpha|beta|rc)', r'\1~\2', remote_ver)
                    version_valid = False
                    for b in gnome_branch.split(','):
                        if fnmatch.fnmatch(remote_ver, b):
                            version_valid = True
                            break
                    if not version_valid:
                        continue
                    rc = rpm.labelCompare((None, remote_ver_tilde, None), (None, newest_remote_version_tilde, None))
                    if rc > 0:
                        newest_remote_version = remote_ver
                        newest_remote_version_tilde = remote_ver_tilde
            if newest_remote_version == '0' and gnome_branch != default_gnome_branch:
                log_error(module, f"No remote versions matching the gnome branch {gnome_branch}")
                log_error(module, "Check modules.xml is looking at the correct branch")
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

            if not args.no_mockbuild:
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
                updates.append((module, version, new_version))
                continue

            rc = run_command (pkg_cache, ['git', 'push'])
            if rc != 0:
                log_error(module, "push")
                continue

            # Try to push the same change to rawhide branch
            if not args.no_rawhide_sync and args.fedora_branch != 'rawhide':
                sync_to_rawhide_branch (module, pkg_cache, args)
                run_command (pkg_cache, ['git', 'checkout', args.fedora_branch])

            # work out release tag
            if args.fedora_branch == "f41":
                pkg_release_tag = 'fc41'
            elif args.fedora_branch == "f42":
                pkg_release_tag = 'fc42'
            elif args.fedora_branch == "f43":
                pkg_release_tag = 'fc43'
            elif args.fedora_branch == "rawhide":
                pkg_release_tag = 'fc44'
            else:
                log_error(module, "Failed to get release tag for", args.fedora_branch)
                continue

            # build package
            if not args.no_build:
                if new_version_tilde:
                    print_info(f"Building {pkg}-{new_version_tilde}-1.{pkg_release_tag}")
                else:
                    print_info(f"Building {pkg}-{version}-1.{pkg_release_tag}")
                if args.buildroot:
                    rc = run_command (pkg_cache, ['fedpkg', 'build', '--nowait', '--target', args.buildroot])
                else:
                    rc = run_command (pkg_cache, ['fedpkg', 'build', '--nowait'])
                if rc != 0:
                    log_error(module, "Build")
                    continue

            # success!
            updates.append((module, version, new_version))
            print_info("Done")

    if (len(updates) == 0):
        print_info("Completed processing without updating any modules")
    else:
        print_info("Summary of updated modules:")
        for (module, oldver, newver) in updates:
            print_info(f"{module}: {oldver} -> {newver}")

    if (len(errors) == 0):
        print_info("Completed processing without any errors")
    else:
        print_info("Summary of errors:")
        for (module, message) in errors:
            print_fail(f"{module}: {message}")


if __name__ == "__main__":
    main()
