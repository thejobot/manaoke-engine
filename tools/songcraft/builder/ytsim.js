// ytsim.js — Denmoku preview YouTube IFrame API simulator.
//
// Served at /preview/<slug>/__ytsim.js; the preview server rewrites the song
// page's `tag.src = "https://www.youtube.com/iframe_api"` to load THIS file
// instead (only when the song's downloaded YT audio exists in corpus/ — see
// server.py _ytsim_audio). The page code is otherwise byte-identical to prod,
// so everything below must behave exactly like the real YT.Player the page
// was written against:
//
//   • window.YT.{Player,PlayerState} then window.onYouTubeIframeAPIReady()
//     (async, after the inline script has finished — we ARE the async script).
//   • events.onReady fires once the media can report duration; onStateChange
//     fires BUFFERING/PLAYING/PAUSED/ENDED with {data, target}; onError on a
//     dead source (the page's CTA-revert path).
//   • getCurrentTime() is a CACHED value refreshed ~every 250ms while playing
//     (YT's infoDelivery cadence — the page's estClock is tuned to extrapolate
//     exactly this regime; handing it a live media clock would make the preview
//     smoother than production and hide real timing bugs), snapped fresh on
//     play/pause/seek/state edges like the real player.
//   • mute()+playVideo() warm path: a muted <video> element is autoplay-exempt
//     in every engine (the page's af3r1o background warm relies on this); if an
//     engine refuses, the play() promise rejection is swallowed and the page's
//     own 1.5s warm-abandon timer takes over — same as prod on mobile WebKit.
//
// The media element is a <video> (not <audio>) so the muted-autoplay exemption
// is guaranteed; it lives inside the #player div the page already hides at
// 1px inside .video-drawer, exactly where YT's iframe would sit. Audio bytes
// = the yt-dlp download of THIS video id (corpus hq_<ytid>.wav) served with
// Range support at __ytsim/audio.wav, so what you hear IS the YT stream.
(function () {
  'use strict';
  var STATES = { UNSTARTED: -1, ENDED: 0, PLAYING: 1, PAUSED: 2, BUFFERING: 3, CUED: 5 };
  var CACHE_MS = 250;                       // infoDelivery-style refresh cadence

  function Player(elId, cfg) {
    var self = this;
    this._ev = (cfg && cfg.events) || {};
    this._cached = 0;                       // the YT-style cached currentTime
    this._readyFired = false;
    this._wantMuted = false;                // mute INTENT (see playVideo)

    var v = document.createElement('video');
    v.setAttribute('playsinline', '');
    v.preload = 'auto';
    v.src = '__ytsim/audio.wav';
    v.style.cssText = 'display:block;width:1px;height:1px;border:0';
    var host = document.getElementById(elId);
    if (host) { host.textContent = ''; host.appendChild(v); }
    else { v.style.display = 'none'; document.body.appendChild(v); }
    this._v = v;

    function snap() { self._cached = v.currentTime || 0; }
    function fireState(d) {
      snap();
      var f = self._ev.onStateChange;
      if (f) { try { f({ data: d, target: self }); } catch (e) {} }
    }
    this._snap = snap;

    v.addEventListener('loadedmetadata', function () {
      if (self._readyFired) return;
      self._readyFired = true;
      var f = self._ev.onReady;
      if (f) { try { f({ target: self }); } catch (e) {} }
      fireState(STATES.CUED);
    });
    v.addEventListener('playing', function () { fireState(STATES.PLAYING); });
    v.addEventListener('waiting', function () { fireState(STATES.BUFFERING); });
    v.addEventListener('pause', function () { if (!v.ended) fireState(STATES.PAUSED); });
    v.addEventListener('ended', function () { fireState(STATES.ENDED); });
    v.addEventListener('error', function () {
      var f = self._ev.onError;
      if (f) { try { f({ data: 150, target: self }); } catch (e) {} }
    });

    setInterval(function () { if (!v.paused && !v.ended) snap(); }, CACHE_MS);
    try { console.info('[ytsim] Denmoku preview: simulating the YouTube stream from the downloaded corpus audio'); } catch (e) {}
  }

  Player.prototype.playVideo = function () {
    // iOS WebKit silences an element that started playback muted (the page's
    // af3r1o background warm) unless the unmute happens DURING a user gesture
    // — the warm-swallow's programmatic unMute() is ignored internally even
    // though .muted reads false afterwards. Re-asserting the muted INTENT
    // synchronously here puts the real unmute inside the tap's gesture stack
    // (togglePlayForCard → playVideo), which WebKit honors. No-op elsewhere.
    this._v.muted = this._wantMuted;
    var p = this._v.play();
    if (p && p.catch) p.catch(function () {});   // autoplay refusal = prod warm-abandon path
  };
  Player.prototype.pauseVideo = function () { this._v.pause(); this._snap(); };
  Player.prototype.stopVideo = function () { this._v.pause(); this._snap(); };
  Player.prototype.seekTo = function (t) {
    try { this._v.currentTime = Math.max(0, +t || 0); } catch (e) {}
    this._snap();
  };
  Player.prototype.getCurrentTime = function () { return this._cached; };
  Player.prototype.getDuration = function () { return this._v.duration || 0; };
  Player.prototype.mute = function () { this._wantMuted = true; this._v.muted = true; };
  Player.prototype.unMute = function () { this._wantMuted = false; this._v.muted = false; };
  Player.prototype.isMuted = function () { return this._v.muted; };
  Player.prototype.setVolume = function (n) { try { this._v.volume = Math.min(1, Math.max(0, n / 100)); } catch (e) {} };
  Player.prototype.getVolume = function () { return Math.round(this._v.volume * 100); };
  Player.prototype.setPlaybackRate = function (r) {
    try { this._v.playbackRate = r; this._v.defaultPlaybackRate = r; } catch (e) {}
  };
  Player.prototype.getPlaybackRate = function () { return this._v.playbackRate || 1; };
  Player.prototype.getAvailablePlaybackRates = function () { return [0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2]; };
  Player.prototype.getPlayerState = function () {
    var v = this._v;
    if (v.ended) return STATES.ENDED;
    if (!v.paused) return (v.readyState < 3) ? STATES.BUFFERING : STATES.PLAYING;
    if (this._readyFired) return (v.currentTime > 0) ? STATES.PAUSED : STATES.CUED;
    return STATES.UNSTARTED;
  };
  Player.prototype.getVideoLoadedFraction = function () {
    var v = this._v;
    try {
      if (v.buffered.length && v.duration) return v.buffered.end(v.buffered.length - 1) / v.duration;
    } catch (e) {}
    return 0;
  };
  Player.prototype.destroy = function () {
    try { this._v.pause(); this._v.removeAttribute('src'); this._v.remove(); } catch (e) {}
  };

  window.YT = { Player: Player, PlayerState: STATES, _denmokuSim: true };
  if (typeof window.onYouTubeIframeAPIReady === 'function') {
    try { window.onYouTubeIframeAPIReady(); } catch (e) {}
  }
})();
