"""Writes tv_afterlife.json (midigen/1): an ambient bed and a sting, hand-written in a Claude Code session.
60 bpm, so one beat = one second and a note's start is its time in the promo. 8 bars of 4/4 = 32 s."""
import json

def at(t, pitch, length, vel):
    """A note at t seconds."""
    return [int(t // 4) + 1, round(t % 4 + 1, 3), pitch, length, vel]

FM9 = ["F3", "Ab3", "C4", "Eb4", "G4"]
DBMAJ9 = ["Db3", "F3", "Ab3", "C4", "Eb4"]
FMAJ9 = ["F3", "A3", "C4", "E4", "G4"]        # the logo: the same root, suddenly major. Wrong in a nice way.

pads = [at(0, FM9, 8, 58), at(8, DBMAJ9, 7.4, 60), at(21, FM9, 3, 50), at(24, FMAJ9, 5.2, 64)]
glass = [at(0.5, ["C4", "G4"], 7.5, 46), at(8.5, ["Ab3", "Eb4"], 6.9, 46), at(24, ["A3", "E4"], 5.2, 52)]
low = [at(0, "F1", 15.4, 62), at(21, "F1", 8.2, 58)]
vibes = [at(2, "C5", 2, 50), at(5.5, "Ab4", 2, 44), at(9, "Eb5", 2, 48), at(12, "C5", 2.5, 42), at(22.5, "G4", 1.5, 36)]
# The strobe (15.5-20): a tremolo cluster that only gets louder. The mix cuts it dead at 20.
tremolo = [at(15.5, ["F3", "Gb3", "C4", "Db4"], 4.5, 90)]
# The sting: a swell into the logo and two bell strikes.
swell = [at(22.6, ["F4", "C5"], 2.4, 70)]
bells = [at(24, "F5", 1.8, 92), at(27.8, "C5", 1.4, 64)]   # the second strike comes after the name is spoken

comp = {
 "format": "midigen/1", "title": "TV Afterlife bed and sting",
 "description": "A dark, slow two-chord bed (F minor 9 to D-flat major 9) with a contrabass pedal and lonely vibraphone, "
                "a tremolo cluster that swells under the strobe, and an F major 9 with two tubular bells for the logo.",
 "arrangement_notes": "60 bpm, beat = 1 s. 0-8 Fm9, 8-15.4 Dbmaj9 (halo pad, bowed glass, contrabass F pedal, vibes). "
                      "15.5-20 tremolo cluster F Gb C Db with an expression ramp to full (the mix cuts it at 20). "
                      "21-24 Fm9 returns softly; 22.6 echo-drops swell; 24 Fmaj9 + tubular bells F5 (24) and C5 (27.8, after the name) for the logo, "
                      "held to 29.2 (the mix cuts at the TV switching off).",
 "tempo": 60, "time_signature": "4/4", "key": "F minor", "bars": 8, "target_seconds": 30,
 "sections": [{"name": "afterlife", "bar": 1, "bars": 4}, {"name": "strobe", "bar": 4, "bars": 2},
              {"name": "somewhere", "bar": 6, "bars": 1}, {"name": "logo", "bar": 7, "bars": 2}],
 "mix": {"reverb": "cathedral"},
 "tracks": [
  {"name": "Halo pad", "role": "bed", "soundfont": "gm", "bank": 0, "program": 94, "channel": 1, "volume": 80, "pan": 50, "reverb": 100, "notes": pads},
  {"name": "Bowed glass", "role": "shimmer", "soundfont": "gm", "bank": 0, "program": 92, "channel": 2, "volume": 62, "pan": 84, "reverb": 110, "notes": glass},
  {"name": "Contrabass", "role": "pedal", "soundfont": "gm", "bank": 0, "program": 43, "channel": 3, "volume": 78, "pan": 64, "reverb": 70, "notes": low},
  {"name": "Vibraphone", "role": "lonely notes", "soundfont": "gm", "bank": 0, "program": 11, "channel": 4, "volume": 70, "pan": 36, "reverb": 110, "notes": vibes},
  {"name": "Tremolo strings", "role": "strobe swell", "soundfont": "gm", "bank": 0, "program": 44, "channel": 5, "volume": 100, "pan": 64, "reverb": 60, "notes": tremolo,
   "controls": [{"bar": 4, "beat": 4.5, "cc": "expression", "value": 25, "to_bar": 5, "to_beat": 4.9, "to_value": 127}]},
  {"name": "Echo drops", "role": "swell", "soundfont": "gm", "bank": 0, "program": 102, "channel": 6, "volume": 70, "pan": 64, "reverb": 100, "notes": swell},
  {"name": "Tubular bells", "role": "sting", "soundfont": "gm", "bank": 0, "program": 14, "channel": 7, "volume": 95, "pan": 70, "reverb": 110, "notes": bells},
 ]}
json.dump(comp, open("tv_afterlife.json", "w"), indent=1)
