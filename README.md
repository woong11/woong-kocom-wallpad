# Woong Kocom Wallpad — 연결 안정화 수정본

[lunDreame/kocom-wallpad](https://github.com/lunDreame/kocom-wallpad) 2.0.5를 기반으로,
사용 중 제어가 멈추고 통합 재시작으로만 복구되는 문제를 개선한 2.0.7 수정본입니다.
원저자의 공식 릴리스는 아닙니다. 변경 사항은 [CHANGELOG.md](CHANGELOG.md)를 참고하세요.

[HACS에서 저장소 열기](https://my.home-assistant.io/redirect/hacs_repository/?owner=woong11&repository=woong-kocom-wallpad&category=integration)

저장소: https://github.com/woong11/woong-kocom-wallpad

## 설치와 기존 설치 전환

HACS 사용자 지정 저장소 유형은 **Integration(통합)** 입니다.
저장소 루트에 `custom_components/kocom_wallpad/`와 `hacs.json`이 있어야 합니다.

1. HA 백업을 만들고 현재 `custom_components/kocom_wallpad` 폴더를 별도로 보관합니다.
2. `https://github.com/woong11/woong-kocom-wallpad`을 HACS 사용자 지정 저장소에 추가합니다.
3. 기존 원본과 수정본은 domain이 같으므로 동시에 설치하지 않습니다. HACS가 같은 domain 중복을 거부하면 원본을 HACS에서 제거한 다음 수정본을 등록·다운로드합니다.
4. 이 과정에서 HA의 **설정 → 기기 및 서비스에 등록된 코콤 통합 항목은 삭제하지 않습니다.**
5. 수정본 다운로드 후 **Home Assistant를 재시작**합니다. Python 파일을 교체한 뒤 통합 다시 로드만으로는 이전 모듈이 남을 수 있습니다.
6. 통합 버전이 2.0.7인지 확인하고, 조명 ON/OFF와 벽 스위치의 상태 반영을 확인합니다.

새 설치는 기기 및 서비스에서 코콤 월패드를 추가하고 EW11 IP와 TCP 포트(기본 8899)를 입력합니다.
USB 시리얼 경로(`/dev/serial/by-id/...`)를 입력하면 포트 대신 9600bps 시리얼을 사용합니다.
이 구성 화면은 기존 host 기반 식별자를 유지합니다. 동일 host의 여러 TCP 포트 구성은 아직 지원하지 않습니다.

HACS의 최소 HA 버전 메타데이터는 원본의 2025.2.2를 유지합니다.
HA 2025.2.2 / Python 3.13 및 HA 2026.10.2 / Python 3.14에서 8개 엔티티 등록·상태 갱신·unavailable 전환·정리를 검증했습니다.
실제 월패드·USB 시리얼 하드웨어와 모든 HA 버전에서 검증한 것은 아닙니다.

## 2.0.7 호환성과 성능 개선

- 시리얼 통신 의존성을 `serialx==1.11.0`으로 변경했습니다. HA 2026.10.2가 사용하는 버전에 맞춰 의존성 충돌 위험을 줄였습니다.
- 최신 HA에서는 게이트웨이 기기를 먼저 등록하고 `via_device_id`로 연결합니다. 구형 HA에는 기존 API를 사용합니다.
- 패킷 필드를 한 번만 해석하고, 데이터 객체에 slots를 적용하며, 미등록 OFF 채널의 불필요한 알림과 전체 복원 캐시의 중복 저장을 줄였습니다.
- 수신 타임아웃은 `asyncio.timeout`으로 명시합니다. Python 3.12 이상에서 기존 wait_for도 내부적으로 같은 방식을 사용하므로 이 변경에 별도 성능 향상 수치를 부여하지 않습니다.
- 전체 점검 결과, 측정 방법과 보류한 변경은 [PERFORMANCE.md](PERFORMANCE.md)에 기록했습니다.
- 기존 엔티티 unique_id, 기기 식별자, 영역 설정은 유지합니다. 기기 목록에 Kocom Gateway가 추가될 수 있습니다.
- 실제 serialx와 Linux 가상 시리얼 포트로 송수신·무수신 타임아웃·종료·재접속을 검증합니다.
- EW11 TCP는 asyncio의 TCP 경로를 유지합니다. 실물 환경에서 응답 속도 향상률은 측정하지 않았습니다.
- 기존 코콤 애드온이 설치되어 있으면 실행 여부를 확인하세요. 같은 EW11에 접속하는 다른 프로그램이 있는지는 설치 목록만으로 판단할 수 없습니다.

## 동작상의 차이

- 연결이 끊기면 자동 재연결하고, 현재 연결에서 해당 장치의 상태를 받은 뒤 사용할 수 있게 표시합니다.
- 재연결 후 잠시 unavailable이면 벽 스위치나 월패드에서 해당 장치를 조작해 상태 패킷을 발생시키세요.
- 조명·콘센트 최초 등록은 원본처럼 ON 상태 관측이 필요합니다.
- 명령 실패는 성공처럼 숨기지 않고 HA 서비스 오류로 표시합니다.
- 가스는 OFF(닫기)만 허용합니다. 엘리베이터는 ON(호출)만 허용하며 자동으로 반복 호출하지 않습니다.
- 난방·에어컨 목표 온도는 현재 구현된 정수 단위(5–40℃)만 허용합니다.
- 조명 밝기 조절은 구현되어 있지 않습니다. 일괄소등 전용 제어는 모델별 확인이 필요합니다.
- 조명 외 일부 제어 패킷의 나머지 필드를 0으로 채우는 원본 동작은 유지합니다. 모델별 의미 확인이 필요합니다.
- 오래된 명령을 재접속 후 일괄 실행하지 않습니다. 실패한 제어는 연결 복구 뒤 다시 요청하세요.

## 장애 확인

기기 및 서비스의 통합 메뉴에서 진단을 다운로드할 수 있습니다.
연결 세대, 수신·송신 작업 생존 상태, 마지막 유효 수신 경과시간, 명령 큐 길이,
재연결·파싱·확인 실패 카운터를 포함합니다. 호스트 주소와 원시 패킷은 포함하지 않습니다.

일시적으로 상세 로그가 필요하면 아래를 적용합니다.

```yaml
logger:
  logs:
    custom_components.kocom_wallpad: debug
```

디버그 로그에는 가정 내 장치 패킷이 포함될 수 있으므로 문제 확인 후 해제하세요.
통합 재시작 전에 진단과 오류 로그를 확보하면 원인을 구분하기 쉽습니다.

## 검증

```bash
python -m unittest discover -s tests -v
python -m compileall -q custom_components
```

회귀 테스트는 HA 경계 객체를 최소 대역으로 교체하고 실제 통합 소스를 import하여 실행합니다.
EOF/자동 재연결은 로컬 TCP 서버로 검증합니다. 별도로 실제 HA 패키지 검증은 다음과 같이 실행합니다.

```bash
# Python 3.13 별도 가상환경
pip install homeassistant==2025.2.2 serialx==1.11.0
python scripts/ha_smoke.py
python scripts/serial_smoke.py
```

이 검증도 전체 HA config-entry 로딩/HACS 설치/실물 RS485 시험을 대체하지 않습니다.

## 되돌리기

이전 `custom_components/kocom_wallpad` 폴더를 복원하고 Home Assistant를 재시작합니다.
엔티티 unique_id 형식은 원본과 같으므로 정상적으로 유지된 설정 항목과 자동화를 재사용합니다.
HACS 업데이트 대상도 원본 저장소로 되돌려 수정본이 다시 덮어쓰지 않도록 합니다.

## 원본 출처

원본 기준 커밋: `d7e7f6578d3ad809bbd5d410d7e3dc110052b77d`.
원저자: lunDreame. 원본 저작권 문구를 유지합니다.

Copyright (c) 2026 lunDreame. All rights reserved.
