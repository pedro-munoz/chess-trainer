/* Build-time configuration.

   scripts/export_static.py overwrites this file in dist/ with MODE = 'static'.
   Mode is fixed at build time rather than probed at runtime: probing costs a
   round-trip on every load and, more to the point, cannot work offline. */

export const MODE = 'server';
export const BUILD_ID = 'dev';
