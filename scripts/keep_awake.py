"""자동화 작업이 도는 동안 PC가 다시 절전으로 들어가지 않게 한다(2026-10-12 추가).

작업 스케줄러가 절전 중인 PC를 깨워도, Windows는 사람 입력이 없으면 약 2분 뒤 다시 재운다
(무인 절전 시간 제한). 그래서 07:30 갱신이 시작 직후 멈췄다가 저녁에 다시 깰 때 이어서 돌거나
(10/10, 10/11), 아예 그날 빠졌다. SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)는
이 프로세스가 살아 있는 동안만 유지되고, 끝나면 자동으로 풀려 다시 정상적으로 절전된다.
"""
import ctypes
import sys

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def keep_awake():
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    except Exception:
        pass
