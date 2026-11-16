#!/bin/bash
# Join HeyGen's four scene clips (their own picture + sound) and lay production 0002's Muzak cue
# (music_0_-1) underneath from the top, ~12 dB under the clips, fading out over the last 1.5 s.
set -e
cd "$(dirname "$0")"
M=/home/mint/ai_tv/productions/0002/music/music_0_-1/music_0_-1.wav
ins=(); chain=""; t=0; n=4
for i in 0 1 2 3; do
  f=$(ls clips/scene${i}_*.mp4)
  d=$(ffprobe -v error -select_streams v:0 -count_frames -show_entries stream=nb_read_frames -of csv=p=0 "$f")
  d=$(python3 -c "print(f'{$d/24:.4f}')")
  t=$(python3 -c "print(f'{$t+$d:.4f}')")
  ins+=(-i "$f")
  chain+="[$i:v]setsar=1,fps=24[v$i];[$i:a]aresample=48000,aformat=channel_layouts=stereo,apad,atrim=0:$d,asetpts=PTS-STARTPTS[a$i];"
done
chain+="[v0][a0][v1][a1][v2][a2][v3][a3]concat=n=$n:v=1:a=1[v][dlg];"
chain+="[$n:a]aresample=48000,apad,atrim=0:$t,afade=t=out:st=$(python3 -c "print($t-1.5)"):d=1.5,loudnorm=I=-29:TP=-3[mus];"
chain+="[dlg]loudnorm=I=-17:TP=-2[d];"
chain+="[d][mus]amix=inputs=2:normalize=0:duration=first,loudnorm=I=-16:TP=-1.5[a]"
ffmpeg -y -v error "${ins[@]}" -i "$M" -filter_complex "$chain" -map "[v]" -map "[a]" -t $t \
  -c:v libx264 -preset slow -crf 17 -pix_fmt yuv420p -c:a aac -b:a 192k -ar 48000 -movflags +faststart \
  "vanishing_cream__heygen-video-1.mp4"
echo "total: $t"
