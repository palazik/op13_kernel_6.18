#!/system/bin/sh
# heat.sh: what keeps the phone busy or warm, for a 6.18 test kernel (run as root on the phone):
#   adb push tools/heat.sh /data/local/tmp/ && adb shell su -c 'sh /data/local/tmp/heat.sh' > heat.txt
# Takes about 40 s; leave the phone alone (screen on or off, as when it gets warm) while it runs.
C=/sys/devices/system/cpu
snap() {	# idle states and CPU time per frequency, to compare before/after
	for s in $C/cpu*/cpuidle/state*; do
		echo "$s $(cat $s/name) usage=$(cat $s/usage) time=$(cat $s/time) rejected=$(cat $s/rejected 2>/dev/null)"
	done
	for p in $C/cpufreq/policy*; do echo "$p: $(tr '\n' ' ' < $p/stats/time_in_state 2>/dev/null)"; done
	echo "suspend: $(cat /sys/power/suspend_stats/success 2>/dev/null) ok, $(cat /sys/power/suspend_stats/fail 2>/dev/null) failed"
	head -1 /proc/stat
}
echo "== $(uname -r) $(date)"
echo "== idle governor: $(cat $C/cpuidle/current_governor 2>/dev/null) (driver $(cat $C/cpuidle/current_driver 2>/dev/null))"
for p in $C/cpufreq/policy*; do
	echo "$p: $(cat $p/scaling_governor) cur=$(cat $p/scaling_cur_freq) min=$(cat $p/scaling_min_freq) max=$(cat $p/scaling_max_freq)"
done
echo "== thermal"
for z in /sys/class/thermal/thermal_zone*; do echo "$(cat $z/type) $(cat $z/temp 2>/dev/null) $(cat $z/mode 2>/dev/null)"; done
echo "== cooling devices in use"
for d in /sys/class/thermal/cooling_device*; do
	s=$(cat $d/cur_state 2>/dev/null); [ "$s" != 0 ] && echo "$(cat $d/type) $s/$(cat $d/max_state)"
done
echo "== gpu: $(cat /sys/class/kgsl/kgsl-3d0/devfreq/cur_freq 2>/dev/null) Hz, busy $(cat /sys/class/kgsl/kgsl-3d0/gpu_busy_percentage 2>/dev/null)"
echo "== battery: $(cat /sys/class/power_supply/battery/current_now) uA, $(cat /sys/class/power_supply/battery/temp) dC, $(cat /sys/class/power_supply/battery/status)"
echo "== before"; snap
cat /proc/interrupts > /data/local/tmp/irq1
sleep 30
echo "== after 30 s"; snap
echo "== battery: $(cat /sys/class/power_supply/battery/current_now) uA"
echo "== interrupts in those 30 s (busiest)"
cat /proc/interrupts | awk 'NR==FNR { if (NR > 1) { s = 0; for (i = 2; i <= NF && $i ~ /^[0-9]+$/; i++) s += $i; a[$1] = s }; next }
	FNR > 1 { s = 0; for (i = 2; i <= NF && $i ~ /^[0-9]+$/; i++) s += $i; d = s - a[$1]; n = $1;
		for (j = i; j <= NF; j++) n = n " " $j; if (d > 0) print d, n }' /data/local/tmp/irq1 - | sort -rn | head -25
rm -f /data/local/tmp/irq1
echo "== busiest threads"
top -b -H -d 5 -n 2 -m 30
echo "== wakeup sources (active or busiest)"
for w in /sys/class/wakeup/wakeup*; do echo "$(cat $w/name) active=$(cat $w/active_time_ms) total=$(cat $w/total_time_ms) count=$(cat $w/active_count)"; done |
	sort -t= -k3 -rn | head -20
echo "== kernel errors and failed suspends"
# (toybox's dmesg has no level filter: by the text)
dmesg 2>&1 | grep -iE 'error|fail|abort|PM: (suspend|Some devices|Wakeup pending)|wakeup.*(pending|irq)' | tail -80
