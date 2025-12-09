# mclazy

mclazy is a script that updates GNOME packages in Fedora. It is indented to be
used by Fedora packagers with membership in the gnome-sig Fedora accounts group.

mclazy can generally update anything that does not require manual intervention,
such as spec file changes. You'll have to manually update whatever mclazy is not
able to do on its own.

## Usage

Generally, to use mclazy you should follow this procedure:

1. Ensure that you are set up for Fedora development. You must be:
   - A Fedora packager,
   - A member of the gnome-sig group or a provenpackager, and
   - Signed into Fedora infrastructure with a valid Kerberos ticket (see `klist` and `fkinit`).
1. Check if the infrastructure is online by trying to visit [dist-git](https://src.fedoraproject.org),
   [Koji](https://koji.fedoraproject.org) and [Bodhi](https://bodhi.fedoraproject.org).
   - If you run mclazy during an infrastructure outage, mclazy might make partial
     updates (i.e. by pushing to dist-git but then never triggering a build).
     This will need manual correction. See [Issue #9](https://github.com/mcatanzaro/mclazy/issues/9).
   - If infrastructure goes down in the middle of a mclazy session and things start
     to fail, make sure to save mclazy's output! If you're not sure how to repair
     things, please reach out in [#workstation:fedoraproject.org](https://matrix.to/#/#workstation:fedoraproject.org)
     on Matrix.
1. If [branches.xml](branches.xml) needs to be updated, update it. There's a few
   situations where this may be necessary. For example:
   - The upcoming Fedora stable has branched from `rawhide`.
   - The upcoming Fedora stable has been released (and you need to update the
     `newstable` and `oldstable` aliases).
   - A branch has gone EOL.
   - The next GNOME alpha has been released and you're updating `rawhide` to use it.
1. Pick the Fedora branch that you want to update.
   - Start with the newest branch and work your way backwards.
   - If `rawhide` is using the same GNOME version as the current `newstable`,
     then mclazy will take care of `rawhide` for you and you should start with
     `newstable` as your branch.
   - Branches are listed in [branches.xml](branches.xml). Note that you can either
     use the real branch name (i.e. `rawhide`, `f43`, `f42`, etc) or aliases
     like `newstable` or `oldstable`.
1. Run `./mclazy.py <branch>` with the branch you've chosen. This runs mclazy in
   "simulation" mode. In this mode, mclazy doesn't push its results to Fedora's
   infrastructure. It still outputs the list of updates that it would apply.
1. Check mclazy's output for bad updates (i.e. development releases on stable
   branches). If you find any, you might have to update the version limits for
   the module in [modules.xml](modules.xml). Make sure to rerun mclazy to verify
   that your new version limits are correct.
1. Obtain a side tag. Side tags are used to group a batch of package updates
   together so that they are all released at once. They aren't strictly necessary,
   but they will make your life easier.
   - Side tags are unique per branch, so each branch will need its own side tag.
   - You can obtain a side-tag by running `fedpkg --release <branch> request-side-tag`.
     Note that you must use the real branch name here, and can't use an alias like
     `newstable` or `oldstable`!
   - Make sure to note down the side tag you're allocated! You'll need it later.
1. Run `./mclazy.py <branch> --no-simulate --side-tag <side-tag-name>`. This
   gives mclazy the ability to interact with Fedora's infrastructure. mclazy will
   update everything that it can.
   - If necessary, mclazy will error out and tell you that you need to specify a
     separate rawhide side tag. Request one for Rawhide and pass it in via
     `--rawhide-side-tag <side-tag-name>`.
1. Packages that need some sort of manual intervention will fail to build. You'll
   need to update and fix these packages manually. Make sure to attach these manual
   builds to your side tag!
    - Don't forget to sync your changes to Rawhide if necessary!
1. Monitor Koji to ensure that your builds succeed. You will need to manually
   intervene if things go wrong.
1. Create a Bodhi update and attach your side tag.
1. Repeat this procedure with the next branch.

Note that mclazy tries to have guardrails and defaults in place to prevent you
from making a few common mistakes. If you _really_ know what you're doing, you
can usually turn off a guardrail by specifying some additional option (check the
output of `./mclazy.py --help` for a list). Otherwise feel free to ask for help
in [#workstation:fedoraproject.org](https://matrix.to/#/#workstation:fedoraproject.org)
on Matrix.
