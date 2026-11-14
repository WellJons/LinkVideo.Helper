"""Safe operator-editable FFmpeg template, preserving the release placeholders."""
from linkvideo_vpn_helper.services.archive_download_methods import validate_manual_ffmpeg_command
import re

def validate_command(command):
    args=validate_manual_ffmpeg_command(command)
    simple={"-y","-nostdin","-hide_banner","-an","-vn","-sn","-dn"}
    values={
        "-loglevel":{"error","warning","info","quiet"},
        "-progress":{"pipe:1"},
        "-i":{"{input}"},
        "-c":{"copy"},"-c:v":{"copy","libx264"},"-c:a":{"copy","aac"},
        "-movflags":{"+faststart","faststart"},
        "-fflags":{"+genpts","+discardcorrupt","+genpts+discardcorrupt"},
        "-avoid_negative_ts":{"make_zero","make_non_negative","auto"},
        "-preset":{"ultrafast","superfast","veryfast","fast","medium"},
    }
    patterns={"-rw_timeout":r"[0-9]{5,9}","-b:a":r"(?:[4-9][0-9]|[1-2][0-9]{2}|320)k","-crf":r"(?:[1-3][0-9]|40)","-map":r"0(?::[av](?::[0-9]{1,2})?)?\??"}
    i=1
    while i<len(args)-1:
        opt=args[i]
        if opt in simple:
            i+=1;continue
        if i+1>=len(args)-1: raise ValueError("У параметра FFmpeg отсутствует значение.")
        val=args[i+1]
        if opt in values and val in values[opt] or opt in patterns and re.fullmatch(patterns[opt],val):
            i+=2;continue
        raise ValueError("Параметр FFmpeg не поддерживается в безопасном режиме: "+opt[:50])
    return command.strip()
