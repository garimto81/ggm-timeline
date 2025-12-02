# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

GGM Timeline Controller는 포커 방송 자동화를 위한 타임라인 이벤트 스케줄러입니다. Google Apps Script WebApp에서 타임라인 데이터를 폴링하고, vMix 리플레이 타임코드를 기준으로 BCode 명령을 Companion(Bitfocus)에 전송합니다.

## Architecture

```
Google Sheet (timeline.csv)
        │ GET 요청 (20초 폴링)
        ▼
ggm-serialize.gas (GAS WebApp)
        │ JSON { ok, rows, heroSlot, villSlot, csvPos }
        ▼
Python Application
├── ggm_io.py          → WebApp 통신, 파일 I/O, 설정 관리
├── ggm_logic.py       → JSON → Event 변환, 블록 파싱
├── ggm_logic_csv.py   → Hero/Villain CSV 생성, 좌석 매핑
└── ggm_timeline_app.py → Tkinter GUI, 스케줄링, 상태 관리
        │
        ├──→ Companion (BCode 전송)
        └──→ vMix (타임코드 폴링)
```

### 모듈 의존성

```
ggm_timeline_app.py
    ├── ggm_logic.py
    │       └── ggm_logic_csv.py
    │               └── ggm_io.py
    ├── ggm_logic_csv.py
    └── ggm_io.py
```

### 핵심 클래스/함수

| 위치 | 이름 | 설명 |
|------|------|------|
| `ggm_timeline_app.py:45` | `TimelineApp` | Tkinter GUI 메인 클래스, 폴링/스케줄링 담당 |
| `ggm_timeline_app.py:33` | `EventState` | 이벤트 실행 상태 추적 (enabled, executed, failed) |
| `ggm_logic.py:16` | `Event` | 타임라인 이벤트 dataclass (time_sec, kind, bcode, label, meta) |
| `ggm_logic.py:648` | `build_timeline_from_rows()` | GAS JSON → Event 리스트 변환 핵심 함수 |
| `ggm_logic_csv.py:9` | `map_seatindex_to_table()` | SeatIndex(0-9) → TableSeat(1-10) 변환 |
| `ggm_logic_csv.py:140` | `build_gtow_csv_from_rows()` | GTO-W 블록에서 Hero/Villain CSV 생성 |

## Commands

```powershell
# 앱 실행
python D:\AI\claude01\ggm_timeline\ggm_timeline_app.py

# GUI 단축키
# F2: 실행 시작
# F3: 실행 중지
```

## Configuration (ggm_config.json)

```json
{
  "serialize_url": "https://script.google.com/macros/s/YOUR_DEPLOY_ID/exec",
  "vmix_ip": "127.0.0.1",
  "vmix_port": "8088",
  "companion_ip": "10.10.100.134",
  "companion_port": "8000",
  "daily_diff_seconds": 0,
  "csv_dir": "C:/GGM$/CSV"
}
```

설정 파일 위치: 스크립트 옆 `ggm_config.json` (1순위) 또는 `%LOCALAPPDATA%\GGM\ggm_config.json`

## Data Schema

### Google Sheet 컬럼 (timeline.csv)

| Column | Index | Field | Description |
|--------|-------|-------|-------------|
| A | 0 | CommandType | GTO-W, MysteryHand, BlindsUp, BreakSkip |
| B | 1 | Delete | `1`이면 삭제 (executed_keys에서 제거) |
| E | 4 | Hand | 블록 구분 키 |
| F | 5 | Time1 | ActionStart (timestamp) |
| G | 6 | Time2 | ActionEnd (timestamp) |
| H | 7 | Seat | SeatIndex (MH: -1=Shuffle, 99=Showdown) |
| I-N | 8-13 | Text1~3, Value1~3 | 추가 데이터 |

### BCode 매핑

| CommandType | BCode | 트리거 |
|-------------|-------|--------|
| GTO-W | 2 | Hero 첫 액션 |
| GTO-W | 4 | Villain 첫 액션 |
| GTO-W | 5 | Villain→Hero 전환 |
| GTO-W | 6 | Hero→Villain 또는 Villain→Villain |
| GTO-W | 7 | Hero→Hero |
| GTO-W | 8 | 핸드 종료 (Hero 마지막) |
| GTO-W | 17 | 핸드 종료 (Villain 마지막) |
| BlindsUp | 20 | 블라인드 업 |
| BreakSkip | 21 | 휴식 건너뛰기 |
| MysteryHands | 22 | Shuffle (시작) |
| MysteryHands | 23 | Fold (좌석별) |
| MysteryHands | 24 | Showdown/End |

### Seat Mapping

SeatIndex(0-9, Vlada=0 기준) → Table Seat(1-10):
```
0→5, 1→6, 2→7, 3→8, 4→9, 5→1, 6→2, 7→3, 8→4, 9→10
```

## Timing Constants

| Constant | Value | 위치 |
|----------|-------|------|
| `QUANT_STEP` | 0.2초 | `ggm_logic.py:745` |
| `POLL_INTERVAL_MS` | 20000ms | `ggm_timeline_app.py:46` |
| `RUN_INTERVAL_MS` | 200ms | `ggm_timeline_app.py:47` |
| Tolerance | 0.6초 | `ggm_timeline_app.py:507` |
| Catchup tolerance | 5.0초 | `ggm_timeline_app.py:508` |

## Dependencies

- Python 3.10+
- tkinter (built-in)
- 외부 라이브러리 없음 (urllib, json, xml.etree 등 표준 라이브러리만)

## GAS 배포

1. [Google Apps Script](https://script.google.com) 접속
2. `ggm-serialize.gas` 코드 전체 복사
3. **배포** > **새 배포** > **웹 앱** (실행: 나, 액세스: 모든 사용자)
4. 배포 URL → `ggm_config.json`의 `serialize_url`에 설정

> **주의**: GAS 코드 수정 후 반드시 **새 배포**로 배포해야 변경사항 반영. 현재 GAS는 ES5 호환 (var, function).

## Troubleshooting

| 증상 | 원인 | 해결 |
|------|------|------|
| GAS `SyntaxError: const` | V8 런타임 비활성화 | 프로젝트 설정 > "Chrome V8" 체크 또는 ES5 코드 사용 |
| GUI 데이터 미표시 | serialize_url 미설정 | 배포 URL 확인, Sheet 이름 `Sheet1` 확인 |
| Delete 미동작 | 플래그 미반환 | GAS `Delete: true` 반환 확인, `build_timeline_from_rows()` 반환값 확인 |
| 이벤트 발화 안됨 | vMix 연결 실패 | vMix IP/Port 확인, Fallback으로 로컬 시계 사용됨 |
