import sys
import math
from pydub import AudioSegment
import struct
import os

class MIDIEvent:
    def __init__(self, event_type, params=None):
        self.event_type = event_type
        self.params = params

    def as_bytes(self):
        if self.event_type == "SetTempo":
            tempo = self.params
            return [0xFF, 0x51, 0x03, (tempo >> 16) & 0xFF, (tempo >> 8) & 0xFF, tempo & 0xFF]
        elif self.event_type == "ProgramChange":
            channel, program = self.params
            return [0xC0 | (channel & 0x0F), program & 0x7F]
        elif self.event_type == "ControlChange":
            channel, cc, value = self.params
            return [0xB0 | (channel & 0x0F), cc & 0x7F, value & 0x7F]
        elif self.event_type == "NoteOn":
            channel, note, velocity = self.params
            return [0x90 | (channel & 0x0F), note & 0x7F, velocity & 0x7F]
        elif self.event_type == "NoteOff":
            channel, note, velocity = self.params
            return [0x80 | (channel & 0x0F), note & 0x7F, velocity & 0x7F]
        elif self.event_type == "EndOfTrack":
            return [0xFF, 0x2F, 0x00]
        else:
            return []

    @staticmethod
    def SetTempo(tempo):
        return MIDIEvent("SetTempo", tempo)

    @staticmethod
    def ProgramChange(channel, program):
        return MIDIEvent("ProgramChange", (channel, program))

    @staticmethod
    def ControlChange(channel, cc, value):
        return MIDIEvent("ControlChange", (channel, cc, value))

    @staticmethod
    def NoteOn(channel, note, velocity):
        return MIDIEvent("NoteOn", (channel, note, velocity))

    @staticmethod
    def NoteOff(channel, note, velocity):
        return MIDIEvent("NoteOff", (channel, note, velocity))

    @staticmethod
    def EndOfTrack():
        return MIDIEvent("EndOfTrack")

def load_media_to_pcm(input_media, pcm_vec):
    audio = AudioSegment.from_file(input_media)
    samplerate = audio.frame_rate
    channels = audio.channels
    samples = list(audio.get_array_of_samples())
    n_frames = len(samples) // channels
    for ch in range(channels):
        pcm_vec.append(samples[ch::channels])
    return samplerate

def get_variable_length_number(bytes_list):
    n = 0
    while True:
        n <<= 7
        x = bytes_list.pop(0)
        n |= (x & 0x7F)
        if x & 0x80 == 0:
            break
    return n

def to_variable_length_bytes(number):
    output = []
    first_pass = True
    working_number = number
    while working_number > 0 or first_pass:
        tmp = working_number & 0x7F
        working_number >>= 7
        if not first_pass:
            tmp |= 0x80
        output.append(tmp)
        first_pass = False
    output.reverse()
    return output

class MidiWriterRaw:
    def __init__(self):
        self.ppqn = 480
        self.tracks = []

    def set_ppqn(self, ppqn):
        self.ppqn = ppqn

    def add_track(self):
        self.tracks.append([])
        return len(self.tracks) - 1

    def push_event(self, track, wait, event):
        if track + 1 >= len(self.tracks):
            for _ in range(len(self.tracks), track + 1):
                self.add_track()
        bytes_list = []
        bytes_list.extend(to_variable_length_bytes(wait))
        bytes_list.extend(event.as_bytes())
        self.tracks[track].extend(bytes_list)

    def save(self, path):
        with open(path, "wb") as file:
            header = bytearray()
            header.extend(b"MThd")
            header.extend(b"\x00\x00\x00\x06")
            header.extend(b"\x00\x01")
            header.extend(bytes([0, len(self.tracks)]))
            header.extend(bytes([(self.ppqn >> 8) & 0xFF, self.ppqn & 0xFF]))
            file.write(header)
            for track in self.tracks:
                bytes_track = bytearray()
                bytes_track.extend(b"MTrk")
                length = len(track)
                bytes_track.extend([
                    (length >> 24) & 0xFF,
                    (length >> 16) & 0xFF,
                    (length >> 8) & 0xFF,
                    length & 0xFF
                ])
                bytes_track.extend(track)
                file.write(bytes_track)
        return

def gen_midi_from_pcm(src, smf, fs):
    smf.set_ppqn(int(fs / 100))
    smf.push_event(0, 0, MIDIEvent.SetTempo(int(60000000 / 6000)))

    if len(src) == 1:
        is_stereo = False
    elif len(src) == 2:
        is_stereo = True
    else:
        raise Exception("not mono or stereo")
    smf.push_event(0, 0, MIDIEvent.ProgramChange(0, 0))
    smf.push_event(1, 0, MIDIEvent.ProgramChange(1, 74))
    if is_stereo:
        smf.push_event(2, 0, MIDIEvent.ProgramChange(2, 0))
        smf.push_event(3, 0, MIDIEvent.ProgramChange(3, 74))
    smf.push_event(0, 0, MIDIEvent.ControlChange(0, 10, 1))
    smf.push_event(1, 0, MIDIEvent.ControlChange(1, 10, 1))
    smf.push_event(2, 0, MIDIEvent.ControlChange(2, 10, 127))
    smf.push_event(3, 0, MIDIEvent.ControlChange(3, 10, 127))

    def amp2vel(point, is_right_channel):
        K = 127.0 * 127.0 / 32768.0
        result = math.sqrt(abs(point) * K)
        u = 0 if point >= 0 else 1
        if is_right_channel:
            u += 2
        return (int(math.floor(result + 0.5)), u)

    note_count = 0
    sys.stdout.write(".________________________________________.\n|")
    sys.stdout.stdout.flush() if hasattr(sys.stdout, "stdout") else sys.stdout.flush()
    total_samples = len(src[0])
    for ch_i in range(len(src)):
        deltatimes = [0, 0, 0, 0]
        for i in range(total_samples):
            vel, u = amp2vel(src[ch_i][i], ch_i == 1)
            if vel != 0:
                d1 = deltatimes[u]
                d2 = 1
                note_count += 1
                smf.push_event(u, d1, MIDIEvent.NoteOn(u, 60, vel))
                smf.push_event(u, d2, MIDIEvent.NoteOff(u, 60, vel))
            for d in range(4):
                deltatimes[d] += 1
            if vel != 0:
                deltatimes[u] = 0
            if i % (total_samples // 20) == 0 and i != 0:
                sys.stdout.write("=")
                sys.stdout.stdout.flush() if hasattr(sys.stdout, "stdout") else sys.stdout.flush()
    for d in range(4):
        smf.push_event(d, 0, MIDIEvent.EndOfTrack())
    sys.stdout.write("|\n")
    return note_count

def main():
    if len(sys.argv) < 2:
        print("Usage: python script.py <path_to_media_file>")
        sys.exit(1)
    path = sys.argv[1]
    the_file = path
    arr_samples = []
    fs = load_media_to_pcm(the_file, arr_samples)
    smf = MidiWriterRaw()
    note_count = gen_midi_from_pcm(arr_samples, smf, fs)
    midi_path = "{}.PCM.mid".format(path)
    smf.save(midi_path)
    print("Note count: {}".format(note_count))

if __name__ == "__main__":
    main()
