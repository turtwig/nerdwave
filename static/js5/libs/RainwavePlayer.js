//  NerdwavePlayer - http://github.com/rmcauley/nerdwave_player

var NerdwavePlayer = (function () {
  "use strict";

  // callback registries
  var callbacks = {
    playing: [],
    stop: [],
    change: [],
    volumeChange: [],
    loading: [],
    stall: [],
    error: [],
    longLoadWarning: [],
  };

  // if the NerdwaveAPI interface is not available, we can fallback to round robin relays
  var hardcodedStations = {
    1: ["https://relay.nerdwave.cc/game"],
    2: ["https://relay.nerdwave.cc/ocremix"],
    3: ["https://relay.nerdwave.cc/covers"],
    4: ["https://relay.nerdwave.cc/chiptune"],
    5: ["https://relay.nerdwave.cc/all"],
  };

  // these friendly names make it easier to use the library as opposed to remembering numbers
  var hardcodedStationToSID = {
    "game": 1,
    "ocr": 2,
    "ocremix": 2,
    "oc remix": 2,
    "cover": 3,
    "covers": 3,
    "chip": 4,
    "chiptune": 4,
    "chiptunes": 4,
    "all": 5,
  };

  var self = {};
  self.debug = false;
  self.isSupported = false;
  self.type = null;
  self.audioElDest = false;
  self.isPlaying = false;
  self.volume = 1.0;
  self.isMuted = false;
  self.mimetype = "";
  self.shuffleURLs = false;

  var streamURLs = [];
  var isMobile =
    navigator.userAgent.toLowerCase().indexOf("mobile") !== -1 ||
    navigator.userAgent.toLowerCase().indexOf("android") !== -1;

  // Chrome on mobile has a really nasty habit of stalling AFTER playback has started
  // And also taking AGES to start the actual playback.
  // We use this flag in a few places to stop Chrome from murdering itself on mobile
  var chromeSpecialFlag = false;

  var createEvent = function (eventType) {
    try {
      return new Event(eventType);
    } catch (e) {
      return { type: eventType };
    }
  };

  // *******************************************************************************************
  //	Initialization And Detection
  // *******************************************************************************************

  var audioEl = document.createElement("audio");
  if ("canPlayType" in audioEl) {
    var canMP3 = audioEl.canPlayType("audio/mpeg");
    if (canMP3 == "maybe" || canMP3 == "probably") {
      self.mimetype = "audio/mpeg";
      self.type = "mp3";
      self.isSupported = true;
    }

    var canVorbis = false;
    if (isMobile) {
      // avoid using Vorbis on mobile devices, since MP3 playback has hardware decoding
    } else {
      canVorbis = audioEl.canPlayType('audio/ogg; codecs="vorbis"');
    }

    // https://bugzilla.mozilla.org/show_bug.cgi?id=1417300
    if (window.navigator.userAgent.indexOf("Firefox/57") !== -1) {
      canVorbis = false;
    }

    if (canVorbis == "maybe" || canVorbis == "probably") {
      self.mimetype = "audio/ogg";
      self.type = "ogg";
      self.isSupported = true;
    }

    // see variable definition of chromeSpecialFlag on why we need to do this
    if (
      navigator.userAgent.indexOf("Chrome") > -1 &&
      navigator.userAgent.indexOf("Android") > -1
    ) {
      chromeSpecialFlag = true;
    }
  }
  audioEl = null;

  // *******************************************************************************************
  //	Private Functions
  // *******************************************************************************************

  var setupAudio = function () {
    if (audioEl) {
      // why call stop: we might still be playing if audioEl is not null.
      // self.stop will recursively call setupAudio though, so you MUST return here
      // to avoid an infinite loop.
      return self.stop();
    }
    self.isPlaying = false;
    audioEl = document.createElement("audio");
    audioEl.addEventListener("abort", onAbort);
    audioEl.addEventListener("ended", onEnded);
    // audioEl.addEventListener("stop", onStop);		// there is no stop event for <audio>
    audioEl.addEventListener("playing", onPlay);
    audioEl.addEventListener("stalled", onStall);
    audioEl.addEventListener("suspend", onSuspend);
    audioEl.addEventListener("waiting", onWaiting);
    audioEl.addEventListener("timeupdate", onTimeUpdate);
    if (self.debug) {
      audioEl.addEventListener("canplay", function () {
        if (self.debug)
          console.log(
            "NerdwavePlayer: <audio> canplay            :: " +
              audioEl.currentSrc
          );
      });
    }
    audioEl.volume = self.isMuted ? 0 : self.volume;
    if (!self.audioElDest) {
      self.audioElDest = document.createElement("div");
    }
    self.audioElDest.appendChild(audioEl);
  };

  var setupAudioSource = function (i, stream_url) {
    var source = document.createElement("source");
    source.setAttribute("src", stream_url);
    source.setAttribute("type", self.mimetype);

    // <source> tags only support addEventListener("error");
    // leave this line commented out.
    // source.addEventListener("playing", self.onPlay);

    // on the final <source> of the audio stream, we throw an error event
    // as the browser will stop trying to play audio
    if (i === streamURLs.length - 1) {
      source.addEventListener("error", function (e) {
        if (self.debug) {
          console.log("NerdwavePlayer: Error on source " + stream_url);
          console.log("NerdwavePlayer: Error on final source.");
        }
        onError(e);
      });
    }
    // otherwise, have it throw a stall
    // (sometimes the source will stall, other times <audio>, sometimes BOTH)
    else {
      source.addEventListener("error", function (e) {
        if (self.debug)
          console.log("NerdwavePlayer: Error on source " + stream_url);
        onStall(e, i);
      });
    }
    return source;
  };

  function shuffle(array) {
    var m = array.length,
      t,
      i;

    // While there remain elements to shuffle…
    while (m) {
      // Pick a remaining element…
      i = Math.floor(Math.random() * m--);

      // And swap it with the current element.
      t = array[m];
      array[m] = array[i];
      array[i] = t;
    }

    return array;
  }

  // *******************************************************************************************
  //	Public Functions
  // *******************************************************************************************

  /**
   * Sets up the library to use a particular Nerdwave station.
   * @param {(number|string)} station - Nerdwave Station ID or Nerdwave Station Name.
   */
  self.useStation = function (station, streamQuery) {
    if (isNaN(parseInt(station)) || !hardcodedStations[parseInt(station)]) {
      if (station.toLowerCase && hardcodedStationToSID[station.toLowerCase()]) {
        station = hardcodedStationToSID[station.toLowerCase()];
      } else {
        console.warn(
          "Unknown Nerdwave Station " + station + ", defaulting to All."
        );
        station = 5;
      }
    }

    streamURLs = [hardcodedStations[station] + "." + self.type + streamQuery];
  };

  /**
   * Sets up the libray to use a list of stream URLs.
   * @param {string[]} newURLs - An array of stream URLs.
   */
  self.useStreamURLs = function (newURLs) {
    streamURLs = newURLs;
  };

  /**
   * Toggles between playing and stopped.
   */
  self.playToggle = function () {
    if (self.isPlaying) self.stop();
    else self.play();
  };

  /**
   * Start playback.
   */
  self.play = function () {
    if (!self.isSupported) {
      console.error(
        "Nerdwave HTML5 Audio Playback is not supported on this browser."
      );
      return;
    }

    var to_return = null;
    if (!self.isPlaying) {
      if (!audioEl) {
        setupAudio();
      }

      if (chromeSpecialFlag) {
        self.dispatchEvent(createEvent("longLoadWarning"));
      }

      if (self.shuffleURLs) {
        shuffle(streamURLs);
      }

      for (var i = 0; i < streamURLs.length; i++) {
        audioEl.appendChild(setupAudioSource(i, streamURLs[i]));
      }

      to_return = audioEl.play();
      self.isPlaying = true;
      self.dispatchEvent(createEvent("loading"));
      self.dispatchEvent(createEvent("change"));
    }
    return to_return;
  };

  /**
   * Stop playback.
   */
  self.stop = function () {
    if (!self.isSupported) return;
    if (!audioEl) return;

    stopAudioConnectError();

    while (audioEl.firstChild) {
      audioEl.removeChild(audioEl.firstChild);
    }
    if (audioEl.parentNode) {
      audioEl.parentNode.removeChild(audioEl);
    }
    audioEl.pause(0); // I forget why I specified the 0 initially
    audioEl.removeAttribute("src"); // nuke all traces of a source from orbit
    try {
      // removing all the <source> elements first and then loading stops the
      // browser from streaming entirely.  anything short of that
      // and the browser will continue to stream in the background, piling up a massive
      // audio buffer.
      audioEl.load();
    } catch (e) {
      // do nothing, we WANT it to fail
    }
    audioEl = null;
    setupAudio();
    self.dispatchEvent(createEvent("stop"));
    self.dispatchEvent(createEvent("change"));
  };

  /**
   * Toggle mute.
   */
  self.toggleMute = function () {
    if (self.isMuted) {
      self.isMuted = false;
      if (audioEl) {
        audioEl.volume = self.volume;
      }
      self.dispatchEvent(createEvent("volumeChange"));
    } else {
      self.isMuted = true;
      if (audioEl) {
        audioEl.volume = 0;
      }
      self.dispatchEvent(createEvent("volumeChange"));
    }
  };

  /**
   * Set volume.
   * @param {number} newVolume - 0.0 to 1.0
   */
  self.setVolume = function (newVolume) {
    self.volume = newVolume;
    if (audioEl && !self.isMuted) {
      audioEl.volume = newVolume;
    }
    self.dispatchEvent(createEvent("volumeChange"));
  };

  // *******************************************************************************************
  //	Event Handlers
  // *******************************************************************************************

  // the stall-related functions have a timeout in order
  // to workaround browser issues that report stalls for VERY brief
  // moments (often 50-70ms).
  // don't let these escape the library unless there's an actual problem.

  // the stall used to be 1000 but had to be raised to 2000 due to issues with onSuspend events over-firing in Firefox 58
  var STALL_DELAY = 2000;

  var stall_timeout;
  var stall_active;
  var stopAudioConnectError = function () {
    if (stall_timeout) {
      if (self.debug)
        console.log("NerdwavePlayer: Stutter on " + audioEl.currentSrc);
      clearTimeout(stall_timeout);
      stall_timeout = null;
    }
    stall_active = false;
  };

  var doAudioConnectError = function (detail) {
    if (stall_active) {
      return;
    } else if (stall_timeout) {
      return;
    } else {
      stall_timeout = setTimeout(function () {
        dispatchStall(detail);
      }, STALL_DELAY);
    }
  };

  var dispatchStall = function (detail) {
    if (self.debug) {
      console.log(
        "NerdwavePlayer: Dispatching stall: " + (detail || "<audio>")
      );
      console.log("NerdwavePlayer: Stalled on URL " + audioEl.currentSrc);
    }
    var evt = createEvent("stall");
    evt.detail = detail;
    self.dispatchEvent(evt);
    stall_timeout = null;
    stall_active = true;
  };

  var onTimeUpdate = function () {
    stopAudioConnectError();
  };

  var onPlay = function () {
    if (self.debug)
      console.log(
        "NerdwavePlayer: <audio> playing            :: " + audioEl.currentSrc
      );
    stopAudioConnectError();
    self.dispatchEvent(createEvent("playing"));
  };

  var onWaiting = function () {
    if (self.debug)
      console.log(
        "NerdwavePlayer: <audio> waiting            ::" + audioEl.currentSrc
      );
    stopAudioConnectError();
    self.dispatchEvent(createEvent("loading"));
  };

  var onEnded = function () {
    if (self.debug)
      console.log(
        "NerdwavePlayer: <audio> ended              :: " + audioEl.currentSrc
      );
    onStop();
    self.play();
  };

  var onAbort = function () {
    if (self.debug)
      console.log(
        "NerdwavePlayer: <audio> aborted            :: " + audioEl.currentSrc
      );
    onStop();
  };

  var onStop = function () {
    if (self.debug)
      console.log(
        "NerdwavePlayer: <audio> stop               :: " + audioEl.currentSrc
      );
    self.stop();
  };

  var onSuspend = function (e) {
    if (self.debug)
      console.log(
        "NerdwavePlayer: <audio> suspend            :: " + audioEl.currentSrc
      );
    onStall(e);
  };

  var onStall = function (e, i) {
    // we need to handle stalls from sources (which have an index)
    // and stalls from the audio element themselves in this function
    // we handle sources so that we know how bad things are.
    // we give errors such as "3/5 sources have failed."

    if (i === undefined) {
      if (self.debug)
        console.log(
          "NerdwavePlayer: <audio> stall              :: " + audioEl.currentSrc
        );
      if (chromeSpecialFlag) {
        // we can ignore <audio> element stalls when Chrome is being special
        // because it is forever stalled for some reason. :/
        if (self.debug)
          console.log(
            "NerdwavePlayer: Ignoring stall due to chromeSpecialFlag."
          );
        return;
      }
    } else {
      if (self.debug)
        console.log(
          "NerdwavePlayer: <source> stall             :: " + audioEl.currentSrc
        );
    }

    var detail;
    if (i !== undefined) {
      detail = " (" + (i + 1) + "/" + streamURLs.length + ")";
    }
    if (self.debug)
      console.log("NerdwavePlayer: Stall event detail: " + detail);
    doAudioConnectError(detail);
  };

  var onError = function (e) {
    if (self.debug)
      console.log(
        "NerdwavePlayer: <audio> error              :: " + audioEl.currentSrc
      );
    stopAudioConnectError();
    self.stop();
    self.dispatchEvent(createEvent("error"));
  };

  // *******************************************************************************************
  //  Event Emitting
  // *******************************************************************************************

  self.dispatchEvent = function (evt) {
    if (!evt) {
      console.error("NerdwavePlayer: No event specified to dispatch.");
      return;
    }
    if (!callbacks[evt.type]) {
      console.error("NerdwavePlayer: Invalid event type.");
      return;
    }
    for (var i = 0; i < callbacks[evt.type].length; i++) {
      callbacks[evt.type][i](evt);
    }
  };

  self.addEventListener = function (evtname, callback) {
    if (!callbacks[evtname]) {
      console.error(
        evtname + " is not a supported event for the Nerdwave Player."
      );
      return;
    }
    callbacks[evtname].push(callback);
  };

  self.removeEventListener = function (evtname, callback) {
    if (!callbacks[evtname]) {
      return;
    }
    while (callbacks[evtname].indexOf(callback) !== -1) {
      callbacks[evtname].splice(callbacks[evtname].indexOf(callback), 1);
    }
  };

  return self;
})();
