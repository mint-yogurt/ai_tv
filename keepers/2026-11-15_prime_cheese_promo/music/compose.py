"""Writes prime_cheese.json (midigen/1): a luxury ambient bed and a logo sting, hand-written in a Claude Code session.
60 bpm, so one beat = one second and a note's start is its time in the promo. 8 bars of 4/4 = 32 s.
Between 12 and 23 there is no music at all: the record scratch kills the bed and the laugh track takes over."""
import json

def at(t, pitch, length, vel):
    """A note at t seconds."""
    return [int(t // 4) + 1, round(t % 4 + 1, 3), pitch, length, vel]

DMAJ9 = ["D3", "F#3", "A3", "C#4", "E4"]
GMAJ7 = ["D3", "G3", "B3", "D4", "F#4"]        # over a D pedal
BM11 = ["D3", "F#3", "A3", "B3", "E4"]
EM9 = ["E3", "G3", "B3", "D4", "F#4"]

# The luxury bed (0-12): warm strings drifting through four chords; the scratch at 12 cuts it mid-chord.
strings = [at(0, DMAJ9, 3.6, 56), at(3.6, GMAJ7, 3, 58), at(6.6, BM11, 2.8, 56), at(9.4, EM9, 3.2, 62)]
cellos = [at(0, "D2", 6.6, 54), at(6.6, "B1", 2.8, 52), at(9.4, "E2", 3.2, 56)]
# Harp: one slow rolled chord per change, not a tune.
harp = [at(0.1 + i * 0.12, p, 2.5, 50) for i, p in enumerate(["D3", "A3", "F#4", "C#5"])] + \
       [at(3.7 + i * 0.12, p, 2.5, 48) for i, p in enumerate(["G3", "D4", "B4", "F#5"])] + \
       [at(6.7 + i * 0.12, p, 2.5, 46) for i, p in enumerate(["B2", "F#3", "D4", "A4"])] + \
       [at(9.5 + i * 0.12, p, 2.5, 50) for i, p in enumerate(["E3", "B3", "G4", "D5"])]
celesta = [at(1.8, "A5", 1.2, 44), at(5.2, "B5", 1.2, 40), at(8.3, "F#5", 1.2, 40), at(10.8, "D6", 1.2, 42)]
# The sting (23-30): a timpani roll into a D major tutti for the logo, then a celesta "ding" as the button.
timp = [at(22.2 + i * 0.1, "D2", 0.1, min(40 + i * 6, 104)) for i in range(13)] + [at(23.5, "D2", 2.5, 112), at(28.9, "A1", 1, 70)]
tutti = ["D3", "A3", "D4", "F#4", "A4", "D5"]
horns = [at(23.5, ["D3", "A3", "D4", "F#4"], 4.8, 92)]
sting_str = [at(23.5, tutti, 5.4, 96)]
sting_harp = [at(23.5 + i * 0.05, p, 3, 80) for i, p in enumerate(["D3", "F#3", "A3", "D4", "F#4", "A4", "D5", "F#5", "A5", "D6"])]
ding = [at(28.9, ["D6", "A6"], 1.1, 90)]

comp = {
 "format": "midigen/1", "title": "Prime Cheese bed and sting",
 "description": "A warm, pretentious string-and-harp bed for a luxury cheese advert (D major 9, G major 7, B minor 11, "
                "E minor 9 over a D pedal), and a timpani roll into a D major tutti with a harp sweep for the logo, "
                "ending on a celesta ding.",
 "arrangement_notes": "60 bpm, beat = 1 s. 0-12.6 bed (strings, cellos, rolled harp chords, celesta twinkles); the mix "
                      "cuts it at 12.0 with a record scratch. 12-22 silence. 22.2 timpani roll, 23.5 D major tutti (strings, "
                      "horns, harp sweep, timpani) for the logo, held to 29; 28.9 celesta D6+A6 ding and a soft timpani A.",
 "tempo": 60, "time_signature": "4/4", "key": "D major", "bars": 8, "target_seconds": 30,
 "sections": [{"name": "luxury", "bar": 1, "bars": 3}, {"name": "laugh track", "bar": 4, "bars": 3},
              {"name": "logo", "bar": 6, "bars": 3}],
 "mix": {"reverb": "hall"},
 "tracks": [
  {"name": "Strings", "role": "bed", "soundfont": "gm", "bank": 0, "program": 49, "channel": 1, "volume": 82, "pan": 58, "reverb": 90, "notes": strings + sting_str},
  {"name": "Cellos", "role": "low bed", "soundfont": "gm", "bank": 0, "program": 42, "channel": 2, "volume": 74, "pan": 72, "reverb": 70, "notes": cellos},
  {"name": "Harp", "role": "rolled chords", "soundfont": "gm", "bank": 0, "program": 46, "channel": 3, "volume": 80, "pan": 40, "reverb": 100, "notes": harp + sting_harp},
  {"name": "Celesta", "role": "twinkles and the ding", "soundfont": "gm", "bank": 0, "program": 8, "channel": 4, "volume": 72, "pan": 84, "reverb": 110, "notes": celesta + ding},
  {"name": "French horns", "role": "sting", "soundfont": "gm", "bank": 0, "program": 60, "channel": 5, "volume": 90, "pan": 52, "reverb": 90, "notes": horns},
  {"name": "Timpani", "role": "roll and hit", "soundfont": "gm", "bank": 0, "program": 47, "channel": 6, "volume": 96, "pan": 64, "reverb": 80, "notes": timp},
 ]}
json.dump(comp, open("prime_cheese.json", "w"), indent=1)
