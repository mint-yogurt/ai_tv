#!/bin/bash
# Join HeyGen's six scene clips (their own picture + sound) and lay production 0003's music cues underneath:
# music_0_0 at 0, music_1_2 from scene 1 to scene 5, music_5_2 from scene 5. Music sits ~12 dB under.
set -e
cd "$(dirname "$0")"
M=/home/mint/ai_tv/productions/0003/music
ins=(); chain=""; t=0; starts=()
for i in 0 1 2 3 4 5; do
  f=$(ls clips/scene${i}_*.mp4)
  d=$(ffprobe -v error -select_streams v:0 -count_frames -show_entries stream=nb_read_frames -of csv=p=0 "$f")
  d=$(python3 -c "print(f'{$d/24:.4f}')")
  starts+=($t); t=$(python3 -c "print(f'{$t+$d:.4f}')")
  ins+=(-i "$f")
  chain+="[$i:v]setsar=1,fps=24[v$i];[$i:a]aresample=48000,aformat=channel_layouts=stereo,apad,atrim=0:$d,asetpts=PTS-STARTPTS[a$i];"
done
chain+="[v0][a0][v1][a1][v2][a2][v3][a3][v4][a4][v5][a5]concat=n=6:v=1:a=1[v][dlg];"
s1=${starts[1]}; s5=${starts[5]}
len1=$(python3 -c "print(f'{$s5-$s1+0.5:.3f}')")
chain+="[6:a]aresample=48000,atrim=0:$(python3 -c "print($s1+1)"),afade=t=out:st=$s1:d=1[m0];"
chain+="[7:a]aresample=48000,atrim=0:$len1,afade=t=in:d=0.8,afade=t=out:st=$(python3 -c "print($len1-1)"):d=1,adelay=$(python3 -c "print(int($s1*1000))"):all=1[m1];"
chain+="[8:a]aresample=48000,adelay=$(python3 -c "print(int($s5*1000))"):all=1[m2];"
chain+="[m0][m1][m2]amix=inputs=3:normalize=0:duration=longest,loudnorm=I=-28:TP=-3[mus];"
chain+="[dlg]loudnorm=I=-17:TP=-2[d];"
chain+="[d][mus]amix=inputs=2:normalize=0:duration=first,loudnorm=I=-16:TP=-1.5[a]"
ffmpeg -y -v error "${ins[@]}" -i $M/music_0_0/music_0_0.wav -i $M/music_1_2/music_1_2.wav -i $M/music_5_2/music_5_2.wav \
  -filter_complex "$chain" -map "[v]" -map "[a]" -t $t -c:v libx264 -preset slow -crf 17 -pix_fmt yuv420p \
  -c:a aac -b:a 192k -ar 48000 -movflags +faststart "the_shart_in_mart_trailer__heygen-video-1.mp4"
echo "scene starts: ${starts[*]}  total: $t"
