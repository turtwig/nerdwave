from nerdwave.events import election
from nerdwave.events import event
from nerdwave import playlist


@event.register_producer
class ShortestElectionProducer(election.ElectionProducer):
    def __init__(self, sid):
        super(ShortestElectionProducer, self).__init__(sid)
        self.elec_type = "ShortestElection"
        self.elec_class = ShortestElection


class ShortestElection(election.Election):
    def _fill_get_song(self, target_song_length):
        return playlist.get_shortest_song(self.sid)
