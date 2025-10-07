var RW_SONG_GAP = 1;

var RWEvent = function (self) {
  "use strict";
  self.type = self.type.toLowerCase();
  self.header_text = null;
  if (!self.used && self.type.indexOf("election") != -1) {
    shuffle(self.songs);
  }
  self.showing_header = true;
  self.height = 0;
  for (var i = 0; i < self.songs.length; i++) {
    self.songs[i]._is_timeline = true;
  }
  NWTemplates.timeline.event(self);
  self.el = self.$t.el;
  self.history = false;
  for (i = 0; i < self.songs.length; i++) {
    self.songs[i] = Song(self.songs[i], self);
    if (
      self.songs[i].$t.art &&
      self.songs[i].$t.art.classList.contains("art_expandable")
    ) {
      self.songs[i].$t.art._reset_router = true;
    }
  }

  self.reflow = function () {
    var running_height = 0;
    for (var i = 0; i < self.songs.length; i++) {
      self.songs[i].el.style[Fx.transform] =
        "translateY(" + running_height + "px)";
      if (self.songs[i].el.classList.contains("now_playing")) {
        running_height += Sizing.song_size_np;
      } else if (self.songs[i].el.classList.contains("song_lost")) {
        if (Prefs.get("l_displose")) {
          running_height += Sizing.song_size + RW_SONG_GAP;
        }
      } else {
        running_height += Sizing.song_size + RW_SONG_GAP;
      }

      self.songs[i].el.style.zIndex = self.songs.length - i;
      self.songs[i].el._zIndex = self.songs.length - i;
    }
    if (self.$t.progress)
      self.$t.progress.style[Fx.transform] =
        "translateY(" + (running_height + 12) + "px)";
  };
  self.reflow();

  self.update = function (json) {
    for (var i in json) {
      if (typeof json[i] !== "object") {
        self[i] = json[i];
      }
    }
    self.type = self.type.toLowerCase();

    if (self.songs) {
      var j;
      for (i = 0; i < self.songs.length; i++) {
        for (j = 0; j < json.songs.length; j++) {
          if (self.songs[i].id == json.songs[j].id) {
            self.songs[i].update(json.songs[j]);
          }
        }
      }
    }
  };

  self.recalculate_height = function () {
    if (self.$t.el.classList.contains("sched_next")) {
      self.height = self.songs.length * (Sizing.song_size + RW_SONG_GAP);
    } else if (self.$t.el.classList.contains("sched_current")) {
      if (Prefs.get("l_displose")) {
        self.height =
          (self.songs.length - 1) * (Sizing.song_size + RW_SONG_GAP) +
          Sizing.song_size_np;
      } else {
        self.height = Sizing.song_size_np + RW_SONG_GAP;
      }
    } else if (self.$t.el.classList.contains("sched_history")) {
      self.height = Sizing.song_size + RW_SONG_GAP;
    }
    if (self.showing_header && !self.history)
      self.height += Sizing.timeline_header_size;
  };

  self.change_to_coming_up = function (is_continuing) {
    self.$t.el.classList.remove("sched_history");
    self.$t.el.classList.remove("sched_current");
    self.$t.el.classList.add("sched_next");
    self.set_header_text(is_continuing ? $l("continued") : $l("coming_up"));
    self.recalculate_height();
  };

  self.change_to_now_playing = function () {
    self.$t.el.classList.remove("sched_next");
    self.$t.el.classList.remove("sched_history");
    self.$t.el.classList.add("sched_current");
    Clock.pageclock = self.$t.clock;
    if (self.songs && self.songs.length > 1) {
      // other places in the code rely on songs[0] to be the winning song
      // make sure we sort properly for that condition here
      self.songs.sort(function (a, b) {
        return a.entry_position < b.entry_position ? -1 : 1;
      });
    }
    if (self.songs[0].autovoted) {
      self.songs[0].remove_autovote();
    }
    self.songs[0].el.classList.add("now_playing");
    for (var i = 1; i < self.songs.length; i++) {
      self.songs[i].el.classList.add("song_lost");
    }
    self.disable_voting();
    self.set_header_text($l("now_playing"));
    self.recalculate_height();
    self.reflow();
    self.progress_bar_start();
  };

  self.change_to_history = function () {
    self.$t.el.classList.remove("sched_current");
    self.$t.el.classList.remove("sched_next");
    self.$t.el.classList.add("sched_history");
    self.history = true;
    self.songs.sort(function (a, b) {
      return a.entry_position < b.entry_position ? -1 : 1;
    });
    self.songs[0].el.classList.remove("now_playing");
    for (var i = 1; i < self.songs.length; i++) {
      self.songs[i].el.classList.add("song_lost");
      Fx.remove_element(self.songs[i].el);
    }
    if (self.$t.progress.parentNode) Fx.remove_element(self.$t.progress);
    self.reflow();
    self.disable_voting();
    self.recalculate_height();
  };

  self.enable_voting = function () {
    var already_voted;
    var self_request = false;
    for (var i = 0; i < self.songs.length; i++) {
      self.songs[i].enable_voting();
      if (
        self.songs[i].el.classList.contains("voting_registered") ||
        self.songs[i].el.classList.contains("voting_clicked")
      ) {
        already_voted = true;
      } else if (self.songs[i].elec_request_user_id == User.id) {
        self_request = i;
      }
    }
    if (self_request !== false && !already_voted) {
      self.songs[self_request].register_vote();
      self.songs[self_request].autovoted = true;
      if (Prefs.get("pwr")) {
        self.songs[self_request].el.classList.add("autovoted");
      } else {
        self.songs[self_request].el.classList.add("voting_registered");
      }
    }
  };

  self.disable_voting = function () {
    for (var i = 0; i < self.songs.length; i++) {
      self.songs[i].disable_voting();
    }
  };

  self.unregister_vote = function () {
    for (var i = 0; i < self.songs.length; i++) {
      self.songs[i].unregister_vote();
    }
  };

  self.set_header_text = function (default_text) {
    var event_desc = Formatting.event_name(self.type, self.name);
    if (event_desc && !self.voting_allowed) {
      self.$t.header.textContent = default_text + " - " + event_desc;
    } else if (event_desc && self.voting_allowed) {
      self.$t.header.textContent = event_desc + " - " + $l("vote_now");
    } else if (self.voting_allowed) {
      self.$t.header.textContent = default_text + " - " + $l("vote_now");
    } else {
      self.$t.header.textContent = default_text;
    }
    self.header_text = self.$t.header.textContent;
    self.$t.header.setAttribute("title", self.header_text);
  };

  self.hide_header = function () {
    self.el.classList.add("no_header");
    self.showing_header = false;
  };

  self.show_header = function () {
    self.el.classList.remove("no_header");
    self.showing_header = true;
  };

  self.progress_bar_start = function () {
    progress_bar_update();
    Clock.pageclock_bar_function = progress_bar_update;
  };

  var progress_bar_update = function () {
    var new_val = Math.min(
      Math.max(
        Math.floor(((self.end - Clock.now) / (self.songs[0].length - 1)) * 100),
        0
      ),
      100
    );
    self.$t.progress_inside.style.width = new_val + "%";
  };

  self.destroy = function () {
    for (var i = 0; i < self.songs.length; i++) {
      self.songs[i].destroy();
    }
  };

  self.live_voting = function (lvsongs) {
    var lvi, si;
    for (lvi = 0; lvi < lvsongs.length; lvi++) {
      for (si = 0; si < self.songs.length; si++) {
        if (lvsongs[lvi].entry_id == self.songs[si].entry_id) {
          self.songs[si].live_voting(lvsongs[lvi]);
        }
      }
    }
  };

  return self;
};
