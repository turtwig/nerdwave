import copy
import os

try:
    import ujson as json
except ImportError:
    import json

from time import time as timestamp

from libs import cache, config, db, log, replaygain
from mutagen.mp3 import MP3
from nerdwave import rating
from nerdwave.playlist_objects import cooldown
from nerdwave.playlist_objects.album import Album
from nerdwave.playlist_objects.artist import Artist
from nerdwave.playlist_objects.metadata import (
    MetadataUpdateError,
    make_searchable_string,
)
from nerdwave.playlist_objects.songgroup import SongGroup

num_songs = {}
num_origin_songs = {}


def set_umask():
    os.setpgrp()
    os.umask(0o02)


def zip_metadata(tag_metadata, kept_metadata):
    new_metadata = copy.copy(tag_metadata)
    for kept in kept_metadata:
        found = False
        for tag in tag_metadata:
            if tag.id == kept.id:
                found = True
        if not found:
            new_metadata.append(kept)
    return new_metadata


# Usable if you want to throw an exception on a file but still continue
# scanning other files.
class PassableScanError(Exception):
    pass


class SongHasNoSIDsException(Exception):
    pass


class SongNonExistent(Exception):
    pass


class SongMetadataUnremovable(Exception):
    pass


class Song:
    sid = 0
    id: None | int
    filename: None | str
    album: None | Album
    artists: list[Artist] = []
    groups: list[SongGroup] = []
    verified = False
    artist_tag: None | str = None
    album_tag: None | str = None
    genre_tag: None | str = None
    data = {}
    data["url"] = None
    data["link_text"] = None
    data["rating_allowed"] = False
    replay_gain: None | str = None
    fake = False

    @classmethod
    def load_from_id(cls, song_id, sid=None, all_categories=False):
        if sid is not None:
            d = db.c.fetch_row(
                "SELECT * FROM r4_songs JOIN r4_song_sid USING (song_id) WHERE r4_songs.song_id = %s AND r4_song_sid.sid = %s",
                (song_id, sid),
            )
        else:
            d = db.c.fetch_row("SELECT * FROM r4_songs WHERE song_id = %s", (song_id,))
            if not d:
                raise SongNonExistent
            sid = d["song_origin_sid"]
        if not d:
            raise SongNonExistent

        try:
            s = cls()
            s.id = song_id
            s.sid = sid
            s.filename = d["song_filename"]
            s.verified = d["song_verified"]
            s.replay_gain = d["song_replay_gain"]
            s.data["sids"] = db.c.fetch_list(
                "SELECT sid FROM r4_song_sid WHERE song_id = %s", (song_id,)
            )
            s.data["sid"] = sid
            s.data["rank"] = None
            s._assign_from_dict(d)

            if "album_id" in d and d["album_id"]:
                s.album = Album.load_from_id_sid(d["album_id"], sid)
            s.artists = Artist.load_list_from_song_id(song_id)
            s.groups = SongGroup.load_list_from_song_id(
                song_id, sid, all_categories=all_categories
            )
        except Exception as e:
            log.exception(
                "song", "Song ID %s failed to load, sid %s." % (song_id, sid), e
            )
            s.disable()
            raise

        return s

    @classmethod
    def load_from_file(cls, filename, sids):
        """
        Produces an instance of the Song class with all album, group, and artist IDs loaded from only a filename.
        All metadata is saved to the database and updated where necessary.
        """

        kept_artists = []
        kept_groups = []
        matched_entry = db.c.fetch_row(
            "SELECT song_id FROM r4_songs WHERE song_filename = %s", (filename,)
        )
        if matched_entry:
            log.debug(
                "playlist",
                "this filename matches an existing database entry, song_id {}".format(
                    matched_entry["song_id"]
                ),
            )
            s = cls.load_from_id(matched_entry["song_id"])
            for metadata in s.artists:
                try:
                    if not metadata.is_tag:
                        kept_artists.append(metadata)
                    metadata.disassociate_song_id(s.id)
                except MetadataUpdateError:
                    pass
            for metadata in s.groups:
                try:
                    if not metadata.is_tag:
                        kept_groups.append(metadata)
                    metadata.disassociate_song_id(s.id)
                except MetadataUpdateError:
                    pass
        elif len(sids) == 0:
            raise SongHasNoSIDsException
        else:
            s = cls()

        s.load_tag_from_file(filename)
        s.save(sids)

        new_artists = Artist.load_list_from_tag(s.artist_tag)
        new_groups = SongGroup.load_list_from_tag(s.genre_tag)

        s.artists = zip_metadata(new_artists, kept_artists)
        s.groups = zip_metadata(new_groups, kept_groups)

        i = 0
        for metadata in s.artists:
            metadata.associate_song_id(s.id, order=i)
            i += 1
        for metadata in s.groups:
            metadata.associate_song_id(s.id)

        s.album = Album.load_from_name(s.album_tag)
        s.album.associate_song_id(s.id)

        s.update_artist_parseable()

        # do not get replay gain earlier in case an exception is thrown above
        # it means a lot of wasted CPU time in that scenario
        if (
            db.c.fetch_var(
                "SELECT song_replay_gain FROM r4_songs WHERE song_id = %s", (s.id,)
            )
            is None
        ):
            s.replay_gain = s.get_replay_gain()
            db.c.update(
                "UPDATE r4_songs SET song_replay_gain = %s WHERE song_id = %s",
                (s.replay_gain, s.id),
            )

        return s

    @classmethod
    def load_from_deleted_file(cls, filename):
        matched_entry = db.c.fetch_row(
            "SELECT song_id FROM r4_songs WHERE song_filename = %s", (filename,)
        )
        if matched_entry and "song_id" in matched_entry:
            s = cls.load_from_id(matched_entry["song_id"])
        else:
            s = None
        return s

    @classmethod
    def create_fake(cls, sid):
        s = cls()
        s.filename = "fake.mp3"
        s.data["title"] = "Test Song %s" % db.c.get_next_id("r4_songs", "song_id")
        s.artist_tag = "Test Artist %s" % db.c.get_next_id("r4_artists", "artist_id")
        s.album_tag = "Test Album %s" % db.c.get_next_id("r4_albums", "album_id")
        s.fake = True
        s.data["length"] = 60
        s.save([sid])
        return s

    def __init__(self):
        """
        A blank Song object.  Please use one of the load functions to get a filled instance.
        """
        self.id = None
        self.filename = None
        self.album = None
        self.artists = []
        self.groups = []
        self.verified = False
        self.artist_tag = None
        self.album_tag = None
        self.genre_tag = None
        self.data = {}
        self.data["url"] = None
        self.data["link_text"] = None
        self.data["rating_allowed"] = False
        self.replay_gain = None
        self.fake = False

    def load_tag_from_file(self, filename):
        """
        Reads ID3 tags and sets object-level variables.
        """

        with open(filename, "rb") as mp3file:
            f = MP3(mp3file, translate=False)
            self.filename = filename

            if not f.tags:
                raise PassableScanError('Song filename "%s" has no tags.' % filename)

            w = f.tags.getall("TIT2")
            if len(w) > 0 and len(str(w[0])) > 0:
                self.data["title"] = str(w[0]).strip()
            else:
                raise PassableScanError(
                    'Song filename "%s" has no title tag.' % filename
                )
            w = f.tags.getall("TPE1")
            if len(w) > 0 and len(str(w[0])) > 0:
                self.artist_tag = str(w[0])
            else:
                raise PassableScanError(
                    'Song filename "%s" has no artist tag.' % filename
                )
            w = f.tags.getall("TALB")
            if len(w) > 0 and len(str(w[0]).strip()) > 0:
                self.album_tag = str(w[0]).strip()
            else:
                raise PassableScanError(
                    'Song filename "%s" has no album tag.' % filename
                )

            w = f.tags.getall("TCON")
            if len(w) > 0 and len(str(w[0])) > 0:
                self.genre_tag = str(w[0])
            w = f.tags.getall("COMM")
            if len(w) > 0 and len(str(w[0])) > 0:
                self.data["link_text"] = str(w[0]).strip()
            w = f.tags.getall("WXXX")
            if len(w) > 0 and len(str(w[0])) > 0:
                self.data["url"] = str(w[0]).strip()
            else:
                self.data["url"] = None

            self.data["length"] = int(f.info.length)

    def get_replay_gain(self):
        return replaygain.get_gain_for_song(self.filename)

    def is_valid(self):
        """
        Lets callee know if this MP3 is valid or not.
        """
        if self.fake:
            self.verified = True
            return True

        if self.filename and os.path.exists(self.filename):
            self.verified = True
            return True
        else:
            self.verified = False
            return False

    def update_artist_parseable(self):
        if not self.artists:
            return
        artist_parseable = []
        for artist in self.artists:
            artist_parseable.append({"id": artist.id, "name": artist.data["name"]})
        artist_parseable = json.dumps(artist_parseable, ensure_ascii=False)
        db.c.update(
            "UPDATE r4_songs SET song_artist_parseable = %s WHERE song_id = %s",
            (artist_parseable, self.id),
        )

    def save(self, sids_override: list[int] | None = None):
        """
        Save song to the database.  Does NOT associate metadata.
        """

        log.debug(
            "playlist", "saving song to database; manual sids? {}".format(sids_override)
        )
        update = False
        if self.id:
            update = True
        else:
            potential_id = None
            # To check for moved/duplicate songs we try to find if it exists in the db
            if self.artist_tag:
                potential_id = db.c.fetch_var(
                    "SELECT song_id FROM r4_songs WHERE song_title = %s AND song_length = %s AND song_artist_tag = %s",
                    (self.data["title"], self.data["length"], self.artist_tag),
                )
            else:
                potential_id = db.c.fetch_var(
                    "SELECT song_id FROM r4_songs WHERE song_title = %s AND song_length = %s",
                    (self.data["title"], self.data["length"]),
                )
            if not config.get("allow_duplicate_song") and potential_id:
                self.id = potential_id
                update = True

        if sids_override:
            self.data["sids"] = sids_override
        elif len(self.data["sids"]) == 0:
            raise SongHasNoSIDsException
        self.data["origin_sid"] = self.data["sids"][0]

        file_mtime = 0
        if not self.fake and self.filename:
            file_mtime = os.stat(self.filename)[8]

        if update:
            log.debug("playlist", "updating existing song_id {}".format(self.id))
            db.c.update(
                "UPDATE r4_songs \
				SET	song_filename = %s, \
					song_title = %s, \
					song_title_searchable = %s, \
					song_url = %s, \
					song_link_text = %s, \
					song_length = %s, \
					song_scanned = TRUE, \
					song_verified = TRUE, \
					song_file_mtime = %s, \
					song_replay_gain = %s, \
					song_origin_sid = %s \
				WHERE song_id = %s",
                (
                    self.filename,
                    self.data["title"],
                    make_searchable_string(self.data["title"]),
                    self.data["url"],
                    self.data["link_text"],
                    self.data["length"],
                    file_mtime,
                    self.replay_gain,
                    self.data["origin_sid"],
                    self.id,
                ),
            )
            if self.artist_tag:
                db.c.update(
                    "UPDATE r4_songs SET song_artist_tag = %s WHERE song_id = %s",
                    (self.artist_tag, self.id),
                )
        else:
            self.id = db.c.get_next_id("r4_songs", "song_id")
            log.debug("playlist", "inserting a new song with id {}".format(self.id))
            db.c.update(
                "INSERT INTO r4_songs \
				(song_id, song_filename, song_title, song_title_searchable, song_url, song_link_text, song_length, song_origin_sid, song_file_mtime, song_verified, song_scanned, song_replay_gain, song_artist_tag) \
				VALUES \
				(%s     , %s           , %s        , %s                   , %s       , %s           , %s         , %s             , %s             , %s           , %s          , %s             , %s)",
                (
                    self.id,
                    self.filename,
                    self.data["title"],
                    make_searchable_string(self.data["title"]),
                    self.data["url"],
                    self.data["link_text"],
                    self.data["length"],
                    self.data["origin_sid"],
                    file_mtime,
                    True,
                    True,
                    self.replay_gain,
                    self.artist_tag,
                ),
            )
            self.verified = True
            self.data["added_on"] = int(timestamp())

        current_sids = db.c.fetch_list(
            "SELECT sid FROM r4_song_sid WHERE song_id = %s", (self.id,)
        )
        log.debug(
            "playlist",
            "database sids: {}, actual sids: {}".format(
                current_sids, self.data["sids"]
            ),
        )
        for sid in current_sids:
            if not self.data["sids"].count(sid):
                db.c.update(
                    "UPDATE r4_song_sid SET song_exists = FALSE WHERE song_id = %s AND sid = %s",
                    (self.id, sid),
                )
        for sid in self.data["sids"]:
            if current_sids.count(sid):
                db.c.update(
                    "UPDATE r4_song_sid SET song_exists = TRUE WHERE song_id = %s AND sid = %s",
                    (self.id, sid),
                )
            else:
                db.c.update(
                    "INSERT INTO r4_song_sid (song_id, sid) VALUES (%s, %s)",
                    (self.id, sid),
                )

    def disable(self):
        if not self.id:
            log.critical("song_disable", "Tried to disable a song without a song ID.")
            return
        log.info("song_disable", "Disabling ID %s / file %s" % (self.id, self.filename))
        db.c.update(
            "UPDATE r4_songs SET song_verified = FALSE WHERE song_id = %s", (self.id,)
        )
        db.c.update(
            "UPDATE r4_song_sid SET song_exists = FALSE WHERE song_id = %s", (self.id,)
        )
        db.c.update("DELETE FROM r4_request_store WHERE song_id = %s", (self.id,))
        if self.album:
            self.album.reconcile_sids()
        if self.groups:
            for metadata in self.groups:
                metadata.reconcile_sids()

    def _assign_from_dict(self, d):
        for key, val in d.items():
            if key.find("song_") == 0:
                key = key[5:]
            # Skip any album-related values
            if key.find("album_") == 0:
                pass
            else:
                self.data[key] = val

    def start_cooldown(self, sid):
        """
        Calculates cooldown based on jfinalfunk's crazy algorithms.
        Cooldown may be overriden by song_cool_* rules found in database.
        Cooldown is only applied if the song exists on the given station
        """

        if (self.sid != sid) or (not self.sid in self.data["sids"]) or sid == 0:
            return

        for metadata in self.groups:
            log.debug(
                "song_cooldown", "Starting group cooldown on group %s" % metadata.id
            )
            metadata.start_cooldown(sid)
        # Albums always have to go last since album records in the DB store cached cooldown values
        if self.album:
            log.debug(
                "song_cooldown", "Starting album cooldown on album %s" % self.album.id
            )
            self.album.start_cooldown(sid)

        cool_time = cooldown.cooldown_config[sid]["max_song_cool"]
        if self.data["cool_override"]:
            cool_time = self.data["cool_override"]
        else:
            cool_rating = self.data["rating"]
            # If no rating exists, give it a middle rating
            if not self.data["rating"] or self.data["rating"] == 0:
                cool_rating = cooldown.cooldown_config[sid]["base_rating"]
            auto_cool = cooldown.cooldown_config[sid]["min_song_cool"] + (
                ((4 - (cool_rating - 1)) / 4.0)
                * (
                    cooldown.cooldown_config[sid]["max_song_cool"]
                    - cooldown.cooldown_config[sid]["min_song_cool"]
                )
            )
            cool_time = (
                auto_cool
                * cooldown.get_age_cooldown_multiplier(self.data["added_on"])
                * self.data["cool_multiply"]
            )

        log.debug(
            "cooldown",
            "Song ID %s Station ID %s cool_time period: %s" % (self.id, sid, cool_time),
        )
        cool_time = int(cool_time + timestamp())
        db.c.update(
            "UPDATE r4_song_sid SET song_cool = TRUE, song_cool_end = %s WHERE song_id = %s AND sid = %s AND song_cool_end < %s",
            (cool_time, self.id, sid, cool_time),
        )
        self.data["cool"] = True
        self.data["cool_end"] = cool_time

        # if 'request_only_end' in self.data and self.data['request_only_end'] != None:
        self.data["request_only_end"] = self.data["cool_end"] + config.get_station(
            sid, "cooldown_request_only_period"
        )
        self.data["request_only"] = True
        db.c.update(
            "UPDATE r4_song_sid SET song_request_only = TRUE, song_request_only_end = %s WHERE song_id = %s AND sid = %s AND song_request_only_end IS NOT NULL",
            (self.data["request_only_end"], self.id, sid),
        )

    def start_election_block(self, sid, num_elections):
        if sid == 0:
            return

        for metadata in self.groups:
            metadata.start_election_block(sid, num_elections)
        if self.album:
            self.album.start_election_block(sid, num_elections)
        self.set_election_block(sid, "in_election", num_elections)

    def set_election_block(self, sid, blocked_by, block_length):
        db.c.update(
            "UPDATE r4_song_sid SET song_elec_blocked = TRUE, song_elec_blocked_by = %s, song_elec_blocked_num = %s WHERE song_id = %s AND sid = %s AND song_elec_blocked_num <= %s",
            (blocked_by, block_length, self.id, sid, block_length),
        )
        self.data["elec_blocked_num"] = block_length
        self.data["elec_blocked_by"] = blocked_by
        self.data["elec_blocked"] = True

    def update_rating(self, skip_album_update=False):
        ratings = db.c.fetch_all(
            "SELECT song_rating_user AS rating, COUNT(user_id) AS count FROM r4_song_ratings JOIN phpbb_users USING (user_id) WHERE song_id = %s AND radio_inactive = FALSE AND song_rating_user IS NOT NULL GROUP BY song_rating_user",
            (self.id,),
        )
        (points, potential_points) = rating.rating_calculator(ratings)

        log.debug(
            "song_rating", "%s ratings for %s" % (potential_points, self.filename)
        )
        if points > 0 and potential_points > config.get("rating_threshold_for_calc"):
            self.data["rating"] = ((points / potential_points) * 4) + 1
            self.data["rating_count"] = potential_points
            log.debug(
                "song_rating",
                "rating update: %s for %s" % (self.data["rating"], self.filename),
            )
            db.c.update(
                "UPDATE r4_songs SET song_rating = %s, song_rating_count = %s WHERE song_id = %s",
                (self.data["rating"], potential_points, self.id),
            )

        if not skip_album_update and self.album:
            self.album.update_rating()

    def add_artist(self, name):
        to_ret = self._add_metadata(self.artists, name, Artist)
        self.update_artist_parseable()
        return to_ret

    def add_album(self, name, sids=None):
        if not sids and "sids" not in self.data:
            raise TypeError(
                "add_album() requires a station ID list if song was not loaded/saved into database"
            )
        elif not sids:
            sids = self.data["sids"]
        if self.album:
            raise Exception("Cannot add more than 1 album association to a song.")
        new_md = Album.load_from_name(name)
        new_md.associate_song_id(self.id, sids)
        self.album = new_md
        return True

    def add_group(self, name):
        return self._add_metadata(self.groups, name, SongGroup)

    def _add_metadata(self, lst, name, cls):
        for metadata in lst:
            if metadata.data["name"] == name:
                return True
        new_md = cls.load_from_name(name)
        new_md.associate_song_id(self.id)
        lst.append(new_md)
        return True

    def remove_artist_id(self, metadata_id):
        toret = self._remove_metadata_id(self.artists, metadata_id)
        self.update_artist_parseable()
        return toret

    def remove_album_id(self, metadata_id):
        if self.album and self.album.id == metadata_id:
            self.album.disassociate_song_id(self.id)
            return True
        raise SongMetadataUnremovable(
            "Found no tag by ID %s that wasn't assigned by ID3." % metadata_id
        )

    def remove_group_id(self, metadata_id):
        return self._remove_metadata_id(self.groups, metadata_id)

    def _remove_metadata_id(self, lst, metadata_id):
        for metadata in lst:
            if metadata.id == metadata_id and not metadata.is_tag:
                metadata.disassociate_song_id(self.id)
                return True
        raise SongMetadataUnremovable(
            "Found no tag by ID %s that wasn't assigned by ID3." % metadata_id
        )

    def remove_artist(self, name):
        toret = self._remove_metadata(self.artists, name)
        self.update_artist_parseable()
        return toret

    def remove_album(self, name):
        if self.album and self.album.data["name"] == name and not self.album.is_tag:
            self.album.disassociate_song_id(self.id)
            return True
        raise SongMetadataUnremovable(
            "Found no tag by name %s that wasn't assigned by ID3." % name
        )

    def remove_group(self, name):
        return self._remove_metadata(self.groups, name)

    def remove_nontag_metadata(self):
        for metadata in self.artists + self.groups:
            if not metadata.is_tag:
                metadata.disassociate_song_id(self.id)

    def _remove_metadata(self, lst, name):
        for metadata in lst:
            if metadata.data["name"] == name and not metadata.is_tag:
                metadata.disassociate_song_id(self.id)
                return True
        raise SongMetadataUnremovable(
            "Found no tag by name %s that wasn't assigned by ID3." % name
        )

    def load_extra_detail(self, sid):
        self.data["rating_rank"] = db.c.fetch_var(
            "SELECT COUNT(song_id) + 1 FROM r4_songs WHERE song_verified = TRUE AND song_rating > %s",
            (self.data["rating"],),
        )
        self.data["request_rank"] = db.c.fetch_var(
            "SELECT COUNT(song_id) + 1 FROM r4_songs WHERE song_verified = TRUE AND song_request_count > %s",
            (self.data["request_count"],),
        )
        self.data["rating_rank_percentile"] = (
            float(num_songs["_total"] - self.data["rating_rank"])
            / float(num_songs["_total"])
        ) * 100
        self.data["rating_rank_percentile"] = max(
            5, min(99, int(self.data["rating_rank_percentile"]))
        )
        self.data["request_rank_percentile"] = (
            float(num_songs["_total"] - self.data["request_rank"])
            / float(num_songs["_total"])
        ) * 100
        self.data["request_rank_percentile"] = max(
            5, min(99, int(self.data["request_rank_percentile"]))
        )

        self.data["rating_histogram"] = {}
        histo = db.c.fetch_all(
            "SELECT "
            "ROUND(((song_rating_user * 10) - (CAST(song_rating_user * 10 AS SMALLINT) %% 5))) / 10 AS rating_user_rnd, "
            "COUNT(song_rating_user) AS rating_user_count "
            "FROM r4_song_ratings JOIN phpbb_users USING (user_id) "
            "WHERE radio_inactive = FALSE AND song_id = %s "
            "GROUP BY rating_user_rnd "
            "ORDER BY rating_user_rnd",
            (self.id,),
        )
        for point in histo:
            if point["rating_user_rnd"]:
                self.data["rating_histogram"][str(point["rating_user_rnd"])] = point[
                    "rating_user_count"
                ]

    def to_dict(self, user=None):
        d = {}
        d["title"] = self.data["title"]
        d["id"] = self.id
        d["rating"] = round(self.data["rating"], 1)
        d["origin_sid"] = self.data["origin_sid"]
        d["link_text"] = self.data["link_text"]
        d["artist_parseable"] = self.data["artist_parseable"]
        d["cool"] = self.data["cool"]
        d["url"] = self.data["url"]
        d["elec_blocked"] = self.data["elec_blocked"]
        d["elec_blocked_by"] = self.data["elec_blocked_by"]
        d["length"] = self.data["length"]

        d["artists"] = []
        d["albums"] = []
        d["groups"] = []
        if self.album:
            d["albums"] = [self.album.to_dict(user)]
        if self.artists:
            for metadata in self.artists:
                d["artists"].append(metadata.to_dict(user))
        if self.groups:
            for metadata in self.groups:
                d["groups"].append(metadata.to_dict(user))

        d["rating_user"] = None
        d["fave"] = None
        d["rating_allowed"] = self.data["rating_allowed"]
        if user:
            d.update(rating.get_song_rating(self.id, user.id))
            if user.data["rate_anything"]:
                d["rating_allowed"] = True

        for v in [
            "entry_id",
            "elec_request_user_id",
            "entry_position",
            "entry_type",
            "entry_votes",
            "elec_request_username",
            "sid",
            "one_up_used",
            "one_up_queued",
            "one_up_id",
            "rating_rank",
            "request_rank",
            "request_count",
            "rating_histogram",
            "rating_count",
            "rating_rank_percentile",
            "request_rank_percentile",
        ]:
            if v in self.data:
                d[v] = self.data[v]

        return d

    def get_all_ratings(self):
        table = db.c.fetch_all(
            "SELECT song_rating_user, song_fave, user_id FROM r4_song_ratings JOIN phpbb_users USING (user_id) WHERE radio_inactive = FALSE AND song_id = %s",
            (self.id,),
        )
        all_ratings = {}
        for row in table:
            all_ratings[row["user_id"]] = {
                "rating_user": row["song_rating_user"],
                "fave": row["song_fave"],
            }
        return all_ratings

    def update_last_played(self, sid):
        if self.album:
            self.album.update_last_played(sid)
        return db.c.update(
            "UPDATE r4_song_sid SET song_played_last = %s WHERE song_id = %s AND sid = %s",
            (timestamp(), self.id, sid),
        )

    def add_to_vote_count(self, votes, sid):
        return db.c.update(
            "UPDATE r4_songs SET song_vote_count = song_vote_count + %s WHERE song_id = %s",
            (votes, self.id),
        )

    def check_rating_acl(self, user):
        if user.id == 1:
            return

        if self.data["rating_allowed"]:
            return

        if user.data["rate_anything"]:
            self.data["rating_allowed"] = True
            return

        acl = cache.get_station(self.sid, "user_rating_acl")
        if acl and self.id in acl and user.id in acl[self.id]:
            self.data["rating_allowed"] = True
        else:
            self.data["rating_allowed"] = False

    def update_request_count(self, sid, update_albums=True):
        count = db.c.fetch_var(
            "SELECT COUNT(*) FROM r4_request_history WHERE song_id = %s", (self.id,)
        )
        db.c.update(
            "UPDATE r4_songs SET song_request_count = %s WHERE song_id = %s",
            (
                count,
                self.id,
            ),
        )

        if update_albums and self.album:
            self.album.update_request_count(sid)

    def update_fave_count(self, sid, update_albums=True):
        count = db.c.fetch_var(
            "SELECT COUNT(*) FROM r4_song_ratings WHERE song_fave = TRUE AND song_id = %s",
            (self.id,),
        )
        db.c.update(
            "UPDATE r4_songs SET song_fave_count = %s WHERE song_id = %s",
            (
                count,
                self.id,
            ),
        )

        if update_albums and self.album:
            self.album.update_fave_count()

    def update_vote_count(self, sid, update_albums=True):
        count = db.c.fetch_var(
            "SELECT COUNT(*) FROM r4_vote_history AND song_id = %s", (self.id,)
        )
        db.c.update(
            "UPDATE r4_songs SET song_vote_count = %s WHERE song_id = %s",
            (
                count,
                self.id,
            ),
        )

        if update_albums and self.album:
            self.album.update_vote_count(sid)

    def length(self):
        return self.data["length"]
