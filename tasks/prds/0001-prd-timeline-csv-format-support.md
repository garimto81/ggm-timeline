# PRD: Timeline Data Schema Migration

**Version**: 1.4
**Date**: 2025-12-02
**Author**: Claude Code
**Status**: Completed

---

## 1. 용어 정의 (중요)

| 용어 | 파일 | 설명 |
|------|------|------|
| **기존 형식** | `sheet1.csv` | 현재 Python이 처리하는 형식 |
| **신규 형식** | `timeline.csv` | 앞으로 사용할 새로운 형식 |

**목표**: 신규 형식(timeline.csv)을 GAS에서 변환하여 Python이 기존과 동일하게 처리할 수 있도록 함

---

## 2. Purpose

GGM Timeline Controller가 **신규 형식(timeline.csv)**의 데이터를 처리할 수 있도록 GAS를 수정한다.
- ~~**Python 수정 금지**~~ → Delete 기능 구현을 위해 최소한의 Python 수정 허용
- GAS가 신규 형식을 읽어서 **기존 JSON 구조와 동일하게 출력**

---

## 3. Architecture

### 3.1 전체 데이터 흐름

```
┌─────────────────────────────────────────────────────────────┐
│  Google Sheet (timeline.csv 형식)                           │
│  CommandType | Delete | Hand | Seat | Time1 | Time2 | ...   │
└──────────────────────┬──────────────────────────────────────┘
                       │ GET 요청 (20초 폴링)
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  ggm-serialize.gas (Google Apps Script)                     │
│  - 신규 스키마 → 기존 JSON 구조 변환                          │
│  - MysteryHand Seat 변환 (-1→Shuffle, 99→Showdown)          │
│  - Delete 플래그 추가                                        │
│  - Hero/Villain 슬롯 계산                                    │
└──────────────────────┬──────────────────────────────────────┘
                       │ JSON { ok, rows, heroSlot, villSlot, csvPos }
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  Python Application                                         │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ ggm_io.py                                           │   │
│  │ - fetch_serialize_rows(): WebApp JSON 수신          │   │
│  │ - send_bcode(): Companion HTTP 전송                 │   │
│  │ - write_csv(): Hero/Villain CSV 파일 생성           │   │
│  └─────────────────────┬───────────────────────────────┘   │
│                        ▼                                    │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ ggm_logic.py                                        │   │
│  │ - _normalize_row(): 필드 정규화 + Delete 플래그 보존 │   │
│  │ - build_timeline_from_rows(): JSON → Event 변환     │   │
│  │ - CommandType별 블록 분리 및 BCode 할당             │   │
│  └─────────────────────┬───────────────────────────────┘   │
│                        ▼                                    │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ ggm_timeline_app.py (Tkinter GUI)                   │   │
│  │ - poll_once(): 20초마다 Serialize 폴링              │   │
│  │ - _run_loop(): 200ms마다 vMix 타임코드 체크         │   │
│  │ - Delete 블록 executed_keys 제거 처리               │   │
│  └─────────────────────┬───────────────────────────────┘   │
└─────────────────────────┼───────────────────────────────────┘
                          │
            ┌─────────────┴─────────────┐
            ▼                           ▼
     ┌────────────┐              ┌────────────┐
     │ Companion  │              │   vMix     │
     │ (Bitfocus) │              │  HTTP API  │
     │ BCode 전송  │              │ 타임코드   │
     └────────────┘              └────────────┘
```

### 3.2 파일 구조

```
D:\AI\claude01\ggm_timeline\
├── ggm_timeline_app.py   # Tkinter GUI (메인 진입점)
├── ggm_logic.py          # 이벤트 빌더 (Rows → Events)
├── ggm_logic_csv.py      # CSV 생성 (Hero/Villain 슬롯)
├── ggm_io.py             # I/O 헬퍼 (설정, HTTP, 파일)
├── ggm_config.json       # 런타임 설정
├── ggm-serialize.gas     # GAS 서버사이드 코드
└── tasks/prds/           # PRD 문서
```

---

## 4. Data Format Comparison

### 4.1 기존 형식 (sheet1.csv)

**헤더:**
```
A열 빈칸 | B=CommandType | C=SeatIndex | D=ActionStart | E=ActionEnd | F=Text1 | G=Text2 | H=Text3 | I=Value1 | J=Value2 | K=Value3
```

**특징:**
- A열 항상 빈칸
- 빈 행으로 블록 구분
- 블록마다 헤더 행 반복
- CommandType 상속 (빈칸이면 이전 값 사용)
- Delete 기능 없음

### 4.2 신규 형식 (timeline.csv)

**헤더:**
```
A=CommandType | B=Delete | C=Level | D=Board | E=Hand | F=Time1 | G=Time2 | H=Seat | I=Text1 | J=Text2 | K=Text3 | L=Value1 | M=Value2 | N=Value3
```

**특징:**
- 연속 행 (빈 행 없음)
- Hand + CommandType 조합으로 블록 구분
- Delete 열 있음 (`1`이면 삭제)
- MysteryHand: Seat=-1(Shuffle), Seat=99(Showdown)

---

## 5. 컬럼 매핑 (신규 → 기존)

| 신규 (timeline.csv) | Index | 기존 JSON 필드 | 변환 로직 |
|---------------------|-------|----------------|----------|
| CommandType | 0 | CommandType | 그대로 |
| Delete | 1 | Delete | `1`이면 `true` |
| Level | 2 | (미사용) | - |
| Board | 3 | (미사용) | - |
| Hand | 4 | Hand | 블록 구분용 |
| Time1 | 5 | ActionStart | 그대로 |
| Time2 | 6 | ActionEnd | 그대로 |
| Seat | 7 | SeatIndex | 변환 필요 (MH) |
| Text1 | 8 | Text1 | 그대로 |
| Text2 | 9 | Text2 | 그대로 |
| Text3 | 10 | Text3 | 그대로 |
| Value1 | 11 | Value1 | 그대로 |
| Value2 | 12 | Value2 | 그대로 |
| Value3 | 13 | Value3 | 그대로 |

---

## 6. MysteryHands 변환 (핵심)

Python이 기대하는 기존 형식으로 GAS에서 변환:

| 신규 (timeline.csv) | 변환 후 (기존 JSON) |
|---------------------|---------------------|
| Seat = `-1` | SeatIndex = `"Open Seat {Text1}"` |
| Seat = `99` | SeatIndex = `"{Text2}"`, Action = `"Showdown/End"` |
| Seat = `0~9` | SeatIndex = 숫자, Action = `"Fold"` |

---

## 7. Delete 기능 (신규)

**신규 형식에만 있는 기능:**
- Delete 열(인덱스 1)에 `1` → 해당 블록 삭제
- 소프트웨어에서 이미 생성된 레일을 삭제해야 함

**구현 완료 (B-2 옵션):**
1. **GAS** (`ggm-serialize.gas`): `Delete: true` 플래그 JSON에 포함
2. **ggm_logic.py**:
   - `_normalize_row()`: Delete 플래그를 `_delete` 키로 보존
   - `build_timeline_from_rows()`: Delete=1 행 필터링 + deleted_keys 반환
   - `KEY_ALIASES`에 `Hand` 필드 추가
3. **ggm_timeline_app.py**:
   - `poll_once()`: deleted_keys 기반 executed_keys 제거 → 재실행 가능

---

## 8. 구현 현황

| 항목 | 상태 | 비고 |
|------|------|------|
| GAS 컬럼 인덱스 매핑 | ✅ 완료 | 신규 스키마 인덱스 적용 |
| GAS ES5 문법 변환 | ✅ 완료 | V8 런타임 비활성화 환경 지원 |
| GAS Delete 플래그 | ✅ 완료 | `Delete: true` |
| GAS MysteryHands 변환 | ✅ 완료 | -1→Shuffle, 99→Showdown |
| Python KEY_ALIASES | ✅ 완료 | `Hand` 필드 추가 |
| Python Delete 필터링 | ✅ 완료 | `ggm_logic.py` 수정 |
| Python executed_keys 삭제 | ✅ 완료 | `ggm_timeline_app.py` 수정 |

---

## 9. 실행 방법

### 9.1 GAS 배포

1. [Google Apps Script](https://script.google.com) 접속
2. `ggm-serialize.gas` 코드 전체 복사
3. 프로젝트에 붙여넣기
4. **배포** > **새 배포** > **웹 앱**
   - 실행 사용자: 나
   - 액세스 권한: 모든 사용자
5. 배포 URL 복사 → `ggm_config.json`의 `serialize_url`에 설정

### 9.2 Python 앱 실행

```powershell
cd D:\AI\claude01\ggm_timeline
python ggm_timeline_app.py
```

### 9.3 설정 파일 (ggm_config.json)

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

---

## 10. 관련 파일

| 파일 | 역할 | 수정 여부 |
|------|------|----------|
| `ggm-serialize.gas` | GAS 스키마 변환 (ES5) | ✅ 수정됨 |
| `ggm_logic.py` | Python 이벤트 빌더 | ✅ 수정됨 |
| `ggm_timeline_app.py` | Python GUI 앱 | ✅ 수정됨 |
| `ggm_io.py` | I/O 헬퍼 | - |
| `ggm_logic_csv.py` | CSV 생성 | - |

---

## 11. 트러블슈팅

### GAS 구문 오류 (SyntaxError: Unexpected token 'const')

**원인**: V8 런타임 비활성화 상태에서 ES6 문법 사용

**해결**:
- 옵션 1: 프로젝트 설정 > "Chrome V8 런타임 사용" 체크
- 옵션 2: ES5 문법으로 작성된 `ggm-serialize.gas` 사용 (현재 버전)

### GUI에 데이터 미표시

**확인 사항**:
1. GAS WebApp 배포 URL이 `ggm_config.json`에 올바르게 설정되었는지
2. Google Sheet의 시트 이름이 `timeline`인지 (GAS의 `SH_MAIN` 상수)
3. GAS 배포 시 "새 버전"으로 배포했는지
4. Python 콘솔에서 오류 메시지 확인
