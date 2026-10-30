// Whether this page and the station still belong together. The station reads the
// page's files from disk as they are asked for, so after an update a browser can get
// new scripts from a station still running its old code (it was not restarted), or
// keep old scripts while the station restarted with new code. Panels would then fail
// in ways that look like faults; instead the page says what to do.

export const RESTART =
  "The station started before its files were updated: restart it, then reload this page.";
export const RELOAD = "The station has been updated since this page was loaded: reload it.";

/** What the operator should do about the station's hello, or "". firstBuild is the
 * build of the station this page first met up to date, null before that. */
export function buildNotice(hello, firstBuild) {
  if (hello.build === undefined || hello.updated) return RESTART; // older than this page
  if (firstBuild !== null && hello.build !== firstBuild) return RELOAD;
  return "";
}
