// Shared-asset proxy (audio): every song-dir — promoted v0N or random preview
// slug — resolves to the ONE shared per-song asset set. No per-slug _redirects
// rule, no duplicated audio. The song is the dir minus its trailing "-<slug>"
// segment: "inochi-mijikashi-v066" -> "inochi-mijikashi", "silhouette-a1b2c3"
// -> "silhouette" -> /songs/_assets/<song>/audio/<path>.
export async function onRequest(context) {
  const { request, params, env } = context;
  const dir = params.dir || '';
  const cut = dir.lastIndexOf('-');
  const song = cut > 0 ? dir.slice(0, cut) : dir;
  const rest = Array.isArray(params.path) ? params.path.join('/') : (params.path || '');
  const url = new URL(request.url);
  url.pathname = `/songs/_assets/${song}/audio/${rest}`;
  return env.ASSETS.fetch(new Request(url.toString(), request));
}
