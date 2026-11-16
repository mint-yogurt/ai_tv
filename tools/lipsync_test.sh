#!/bin/bash
# Lip-sync test on the GTX 970: Wav2Lip and SadTalker, two clips each, into test_outputs.
# Model files load as plain data only (TORCH_FORCE_WEIGHTS_ONLY_LOAD), so they can't run code.
set -u
TOOLS=/home/mint/ai_tv/tools
T=/home/mint/ai_tv/test_outputs/video/2026-11-11_lipsync
PY=$TOOLS/lipsync-venv/bin/python
export TORCH_FORCE_WEIGHTS_ONLY_LOAD=1
mkdir -p "$T/raw"

gpu_peak() {  # sample GPU memory while a command runs; print the peak in MiB
  local peak=0 m
  while kill -0 "$1" 2>/dev/null; do
    m=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | head -1)
    [ "$m" -gt "$peak" ] && peak=$m
    sleep 1
  done
  echo "$peak"
}

run() {  # name, then the command
  local name=$1; shift
  echo "== $name"
  local start=$(date +%s)
  "$@" > "$T/raw/$name.log" 2>&1 &
  local pid=$!
  local peak=$(gpu_peak $pid)
  wait $pid; local rc=$?
  echo "   exit $rc, $(( $(date +%s) - start ))s, GPU peak ${peak} MiB (log: raw/$name.log)"
  [ $rc -ne 0 ] && tail -5 "$T/raw/$name.log"
}

cd $TOOLS/wav2lip
for who in gary:gary_line earl:poots_line; do
  img=${who%%:*}; aud=${who##*:}
  run "wav2lip_$img" $PY inference.py --checkpoint_path checkpoints/wav2lip.pth \
    --face "$T/inputs/$img.png" --audio "$T/inputs/$aud.wav" --outfile "$T/raw/wav2lip_$img.mp4" \
    --static True --fps 30 --pads 0 15 0 0 --face_det_batch_size 4 --wav2lip_batch_size 32
done

cd $TOOLS/sadtalker
run sadtalker_gary $PY inference.py --driven_audio "$T/inputs/gary_line.wav" --source_image "$T/inputs/gary.png" \
  --result_dir "$T/raw/sadtalker_gary" --still --preprocess full --size 256 --batch_size 2
run sadtalker_earl $PY inference.py --driven_audio "$T/inputs/poots_line.wav" --source_image "$T/inputs/earl.png" \
  --result_dir "$T/raw/sadtalker_earl" --preprocess full --size 256 --batch_size 2
echo "done"
