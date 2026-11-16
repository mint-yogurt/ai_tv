"""Writes cereal_killers.json (midigen/1). Hand-composed in a Claude Code session (the manual backend).
120 bpm, 4/4, one bar = 2.0 s, 15 bars = 30.0 s. Every bar line is a picture cut."""
import json

def n(bar, beat, pitch, length, vel): return [bar, beat, pitch, length, vel]

# The jingle motif: minor on the music box (bars 1-4), major on synth brass (bars 9-13), music box again in the tag.
box = [n(1,1,"A5",.5,70), n(1,1.5,"C6",.5,62), n(1,2,"E6",.5,66), n(1,2.5,"A6",1.5,74),
       n(2,1,"G#6",.5,68), n(2,1.5,"E6",.5,60), n(2,2,"F6",.5,64), n(2,2.5,"E6",1.5,70),
       n(3,1,"A5",.5,66), n(3,1.5,"C6",.5,60), n(3,2,"F6",1,68), n(3,3,"E6",.5,60), n(3,3.5,"C6",.5,56),
       n(4,1,"B5",1,64), n(4,2,"G#5",1,58), n(4,3,"E5",1.9,52)]
pad = [n(1,1,["A3","C4","E4"],8,60), n(3,1,["F3","A3","C4"],4,60), n(4,1,["E3","G#3","B3","D4"],4,66)]
# Heartbeat: lub-dub on the timpani, then a roll into the lineup.
timp = []
for bar, p in ((1,"A2"),(2,"A2"),(3,"F2")):
    timp += [n(bar,1,p,.5,96), n(bar,1.5,p,.5,62), n(bar,3,p,.5,90), n(bar,3.5,p,.5,58)]
timp += [n(4,1+i*.25,"E2",.25,50+i*5) for i in range(12)]
# Lineup: one hit per suspect, every half bar (8, 9, ... 13 s), rising by a semitone each time.
roots = ["A4","Bb4","B4","C5","C#5","D5"]
hits, lineup_timp, lineup_kit = [], [], []
for i, r in enumerate(roots):
    bar, beat = 5 + i // 2, 1 if i % 2 == 0 else 3
    hits.append(n(bar,beat,r,1,112))
    lineup_timp.append(n(bar,beat,"A2",.5,115))
    lineup_kit += [n(bar,beat,"kick",.5,120), n(bar,beat,"crash",.5,70), n(bar,beat,"clap",.5,118), n(bar,beat,"splash",.5,100)]  # the camera flash
for bar in (5,6,7):
    lineup_kit += [n(bar,b,"woodblock_high",.25,70 if b % 1 == 0 else 45) for b in (1,1.5,2,2.5,3,3.5,4,4.5)]
drone = [n(5,1,"A1",12,70)]
# Bar 8: the mastermind. Dun, dun, DUNNN.
dun = [n(8,1,["E3","E4"],.6,112), n(8,2,["F3","F4"],.6,112), n(8,3,["D#3","D#4"],1.9,124)]
dun_timp = [n(8,1,"E2",.5,118), n(8,2,"F2",.5,118)] + [n(8,3+i*.25,"D#2",.25,70+i*6) for i in range(8)]
# Bars 9-13: the full cartoon-rock version. A | F#m | D | E | D E  -> bar 14 A.
chords = {9:"A",10:"F#",11:"D",12:"E"}
power = {"A":["A2","E3","A3"],"F#":["F#2","C#3","F#3"],"D":["D2","A2","D3"],"E":["E2","B2","E3"]}
bassn = {"A":"A1","F#":"F#1","D":"D2","E":"E1"}
gtr, bass = [], []
for bar, c in list(chords.items()) + [(13,"D")]:
    span = (1,1.5,2,2.5,3,3.5,4,4.5) if bar != 13 else (1,1.5,2,2.5)
    for b in span:
        gtr.append(n(bar,b,power[c],.45,100 if b % 1 == 0 else 80))
        bass.append(n(bar,b,bassn[c],.45,100 if b % 1 == 0 else 82))
for b in (3,3.5,4,4.5):
    gtr.append(n(13,b,power["E"],.45,105)); bass.append(n(13,b,bassn["E"],.45,100))
lead = [n(9,1,"A4",.5,105), n(9,1.5,"C#5",.5,95), n(9,2,"E5",.5,100), n(9,2.5,"A5",1.5,110),
        n(10,1,"A5",.5,100), n(10,1.5,"F#5",.5,92), n(10,2,"E5",.5,96), n(10,2.5,"C#5",1.5,100),
        n(11,1,"D5",.5,100), n(11,1.5,"F#5",.5,92), n(11,2,"A5",1,104), n(11,3,"F#5",.5,92), n(11,3.5,"A5",.5,98),
        n(12,1,"B5",1,106), n(12,2,"G#5",.5,94), n(12,2.5,"E5",.5,94), n(12,3,"B4",1,98), n(12,4,"E5",.5,96),
        n(13,1,"A5",.5,104), n(13,1.5,"F#5",.5,94), n(13,2,"D5",1,100), n(13,3,"E5",.5,100), n(13,3.5,"G#5",.5,106), n(13,4,"B5",.9,114)]
rock = []
for bar in range(9,14):
    for b in (1,1.5,2,2.5,3,3.5,4,4.5):
        rock.append(n(bar,b,"closed_hat",.25,90 if b % 1 == 0 else 60))
    rock += [n(bar,1,"kick",.5,118), n(bar,2.5,"kick",.5,95), n(bar,3,"kick",.5,112), n(bar,2,"snare",.5,112), n(bar,4,"snare",.5,115)]
rock[0:0] = [n(9,1,"crash",.5,118)]
rock += [n(11,1,"crash",.5,100)]
rock += [n(13,4,"tom_high",.25,105), n(13,4.25,"tom_mid",.25,108), n(13,4.5,"tom_low",.25,112), n(13,4.75,"tom_floor",.25,118)]
rock += [n(8,4.5,"snare",.25,80), n(8,4.75,"snare",.25,100)]
# Bar 14: the logo slam, then a held A while Kige speaks. Bar 15: "Only on ChupTV" button.
slam_kit = [n(14,1,"kick",.5,127), n(14,1,"crash",.5,127), n(14,1,"china",.5,90), n(15,3,"kick",.5,120), n(15,3,"crash2",.5,110)]
slam_hit = [n(14,1,"A4",1,124), n(15,3,"A4",1,120)]
slam_gtr = [n(14,1,power["A"],1.5,115), n(15,3,["A2","E3","A3","C#4"],1.9,110)]
slam_bass = [n(14,1,"A1",1.5,115), n(15,3,"A1",1.9,110)]
hold = [n(14,1,["A3","C#4","E4","A4"],8,62)]
tag_box = [n(14,2.5,"A5",.5,60), n(14,3,"C#6",.5,56), n(14,3.5,"E6",.5,58), n(14,4,"A6",1,62)]
slam_timp = [n(14,1,"A2",.5,124), n(15,2.5,"E2",.25,80), n(15,2.75,"E2",.25,95), n(15,3,"A2",.5,124)]

comp = {
 "format": "midigen/1", "title": "Cereal Killers promo",
 "description": "A minor-key music-box cereal jingle over a timpani heartbeat, a rising orchestra-hit police lineup, "
                "a dun-dun-DUNNN, then the same jingle as full 90s cartoon rock, a logo slam and a button.",
 "arrangement_notes": "120 bpm, bar = 2 s, cuts on bar lines. Bars 1-4 A minor (Am Am F E7), music box motif, pad, "
                      "timpani heartbeat, roll into bar 5. Bars 5-7 lineup: orchestra hit + timpani + kick every half bar "
                      "rising A Bb B C C# D over a contrabass A drone and woodblock ticks. Bar 8 dun dun DUNNN (E F D#) on "
                      "trombones/horns + timpani roll, snare pickup. Bars 9-13 A major rock (A F#m D E | D E): distortion "
                      "power chords and picked bass in eighths, synth brass plays the motif in major, Power kit, tom fill. "
                      "Bar 14 slam on A, strings hold under the voiceover, music box tags the motif; bar 15 beat 3 final A button (after the voiceover).",
 "tempo": 120, "time_signature": "4/4", "key": "A minor", "bars": 15, "target_seconds": 30,
 "sections": [{"name":"crime scene","bar":1,"bars":4},{"name":"lineup","bar":5,"bars":3},{"name":"mastermind","bar":8,"bars":1},
              {"name":"rock","bar":9,"bars":5},{"name":"logo","bar":14,"bars":1},{"name":"button","bar":15,"bars":1}],
 "mix": {"reverb": "hall"},
 "tracks": [
  {"name":"Music box","role":"motif","soundfont":"gm","bank":0,"program":10,"channel":1,"volume":100,"pan":44,"reverb":80,"notes":box+tag_box},
  {"name":"Halo pad","role":"bed","soundfont":"gm","bank":0,"program":94,"channel":2,"volume":72,"pan":84,"reverb":90,"notes":pad},
  {"name":"Timpani","role":"heartbeat and hits","soundfont":"gm","bank":0,"program":47,"channel":3,"volume":110,"pan":64,"reverb":60,
   "notes":timp+lineup_timp+dun_timp+slam_timp},
  {"name":"Orchestra hit","role":"lineup stabs","soundfont":"gm","bank":0,"program":55,"channel":5,"volume":118,"pan":64,"reverb":70,
   "notes":hits+slam_hit},
  {"name":"Contrabass","role":"drone","soundfont":"gm","bank":0,"program":43,"channel":6,"volume":80,"pan":64,"reverb":60,"notes":drone},
  {"name":"Trombones","role":"dun dun dunnn","soundfont":"gm","bank":0,"program":57,"channel":7,"volume":98,"pan":54,"reverb":60,"notes":dun},
  {"name":"Horns","role":"dun dun dunnn doubled","soundfont":"gm","bank":0,"program":60,"channel":8,"volume":84,"pan":74,"reverb":60,"notes":dun},
  {"name":"Synth brass","role":"lead motif","soundfont":"gm","bank":0,"program":62,"channel":4,"volume":96,"pan":70,"reverb":40,"notes":lead},
  {"name":"Distortion guitar","role":"power chords","soundfont":"gm","bank":0,"program":30,"channel":9,"volume":92,"pan":40,"reverb":25,"notes":gtr+slam_gtr},
  {"name":"Picked bass","role":"bass","soundfont":"gm","bank":0,"program":34,"channel":11,"volume":105,"pan":64,"reverb":10,"notes":bass+slam_bass},
  {"name":"Strings","role":"final hold","soundfont":"gm","bank":0,"program":48,"channel":12,"volume":80,"pan":88,"reverb":80,"notes":hold,
   "controls":[{"bar":14,"beat":1,"cc":"expression","value":110},{"bar":15,"beat":3,"cc":"expression","value":110,"to_bar":15,"to_beat":4.9,"to_value":20}]},
  {"name":"Drums","role":"kit","soundfont":"gm","bank":128,"program":16,"channel":10,"volume":110,"pan":64,"reverb":30,"notes":lineup_kit+rock+slam_kit},
 ]}
json.dump(comp, open("cereal_killers.json","w"), indent=1)
