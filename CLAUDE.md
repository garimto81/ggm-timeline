# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

GGM Timeline Controller는 포커 방송 자동화를 위한 타임라인 이벤트 스케줄러입니다. Google Apps Script WebApp에서 타임라인 데이터를 폴링하고, vMix 리플레이 타임코드를 기준으로 BCode 명령을 Companion(Bitfocus)에 전송합니다.

## Architecture

### 전체 데이터 흐름

```
Google Sheet (timeline.csv)
        │ GET 요청 (20초 폴링)
        ▼
ggm-serialize.gas (GAS WebApp)
        │ JSON { ok, rows, heroSlot, villSlot, csvPos }
        ▼
Python Application
├── ggm_io.py          → WebApp 통신, 파일 I/O
├── ggm_logic.py       → JSON → Event 변환
├── ggm_logic_csv.py   → Hero/Villain CSV 생성
└── ggm_timeline_app.py → GUI, 스케줄링
        │
        ├──→ Companion (BCode 전송)
        └──→ vMix (타임코드 폴링)
```

### 파일 구조

```
ggm_timeline/
├── ggm_timeline_app.py   # Tkinter GUI 메인 앱 (진입점)
├── ggm_logic.py          # 이벤트 빌더 (Rows → Events)
├── ggm_logic_csv.py      # CSV 생성 (Hero/Villain 슬롯 결정)
├── ggm_io.py             # I/O 헬퍼 (설정, HTTP, 파일)
├── ggm_config.json       # 런타임 설정
├── ggm-serialize.gas     # Google Apps Script (ES5 호환)
└── tasks/prds/           # PRD 문서
```

## Commands

```powershell
# 앱 실행
cd D:\AI\claude01\ggm_timeline
python ggm_timeline_app.py

# 설정 파일 위치
# 1순위: 스크립트 옆 ggm_config.json
# 2순위: $env:APPDATA\..\Local\GGM\ggm_config.json
```

## GAS 배포

1. [Google Apps Script](https://script.google.com) 접속
2. `ggm-serialize.gas` 코드 전체 복사
3. **배포** > **새 배포** > **웹 앱**
   - 실행 사용자: 나
   - 액세스 권한: 모든 사용자
4. 배포 URL → `ggm_config.json`의 `serialize_url`에 설정

> **주의**: GAS 코드 수정 후 반드시 **새 배포**로 배포해야 변경사항 반영

## Key Configuration (ggm_config.json)

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

## Data Schema (timeline.csv 신규 형식)

| Column | Index | Field | Description |
|--------|-------|-------|-------------|
| A | 0 | CommandType | GTO-W, MysteryHand, BlindsUp, BreakSkip |
| B | 1 | Delete | `1`이면 삭제 |
| C | 2 | Level | (미사용) |
| D | 3 | Board | (미사용) |
| E | 4 | Hand | 블록 구분 키 |
| F | 5 | Time1 | ActionStart |
| G | 6 | Time2 | ActionEnd |
| H | 7 | Seat | SeatIndex (MH: -1=Shuffle, 99=Showdown) |
| I-N | 8-13 | Text1~3, Value1~3 | 추가 데이터 |

## Event Types (CommandType)

| CommandType | BCode | Description |
|-------------|-------|-------------|
| GTO-W | 2,4,5,6,7,8,17 | Heads-Up 포커 핸드 재생 |
| MysteryHands | 22,23,24 | 멀티웨이 핸드 오버레이 |
| BlindsUp | 20 | 블라인드 레벨 업 |
| BreakSkip | 21 | 휴식 건너뛰기 |

## Important Constants

| Constant | Value | Description |
|----------|-------|-------------|
| QUANT_STEP | 0.2초 | 이벤트 시간 양자화 단위 |
| POLL_INTERVAL_MS | 20000 | Serialize 폴링 주기 |
| RUN_INTERVAL_MS | 200 | 이벤트 체크 주기 |
| Tolerance | 0.6초 | 이벤트 발화 허용 오차 |

## Seat Mapping

SeatIndex(0-9, Vlada=0 기준) → Table Seat(1-10):
```
0→5, 1→6, 2→7, 3→8, 4→9, 5→1, 6→2, 7→3, 8→4, 9→10
```

## Dependencies

- Python 3.10+
- tkinter (built-in)
- 외부 라이브러리 없음 (urllib, json, xml.etree 등 표준 라이브러리만)

## External Integrations

| Service | Protocol | Usage |
|---------|----------|-------|
| vMix | HTTP `/api/` | 리플레이 타임코드 폴링 |
| Companion | HTTP `/press/bank/{page}/{btn}` | BCode 전송 |
| GAS WebApp | HTTP GET/POST | Serialize, MH Plan |

## Troubleshooting

### GAS 구문 오류 (SyntaxError: Unexpected token 'const')

**원인**: V8 런타임 비활성화 상태

**해결**:
- 옵션 1: 프로젝트 설정 > "Chrome V8 런타임 사용" 체크
- 옵션 2: 현재 `ggm-serialize.gas`는 ES5 호환 (var, function)

### GUI에 데이터 미표시

1. `serialize_url` 확인 (배포 URL)
2. Google Sheet 시트 이름 확인 (`Sheet1`)
3. GAS "새 버전" 배포 여부 확인
4. Python 콘솔 오류 메시지 확인

### Delete 기능 미동작

- GAS에서 `Delete: true` 플래그 반환 확인
- `ggm_logic.py`의 `KEY_ALIASES`에 `Hand` 키 존재 확인
- `build_timeline_from_rows()` 반환값이 `(events, deleted_keys)` 튜플인지 확인

## Related Documents

- PRD: `tasks/prds/0001-prd-timeline-csv-format-support.md`
