mclazy is a script that updates GNOME packages in Fedora. It is intended to be
used by Fedora packagers with membership in the gnome-sig Fedora accounts group.
mclazy can generally update anything that does not require manual intervention,
such as spec file changes. You'll have to manually update whatever mclazy is not
able to do on its own.

As of September 2025, this script is a little awkward to use. Here are some
temporary instructions intended to help you avoid the bigger footguns:

 * Always use --fedora-branch. --fedora-branch f43 will merge changes from f43
   to rawhide. If you omit --fedora-branch, then the f43 and rawhide branches
   will permanently diverge, necessitating cherry-picks instead of merge
   commits, which is annoying.
 * Always use --no-rawhide-sync if using --fedora-branch f42 or --fedora-branch
   f41.
 * Start by using --simulate to ensure the output looks reasonable. Then you can
   confidently use --no-simulate.

Michael Catanzaro will endeavor to make the script easier to use safely, and
write better instructions.
