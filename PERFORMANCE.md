# 전체 코드 및 라이브러리 성능 점검 — 2.0.7

## 범위와 결론

transport, controller, gateway, models, entity_base, 6개 엔티티 플랫폼,
설정 흐름, 초기화/종료, 진단, 의존성 및 HA 상태 복원 경로를 점검했다.
외부 직접 의존성은 시리얼 라이브러리 하나이며, 나머지는 Python 표준 라이브러리와
HA가 제공하는 API/voluptuous다. 라이브러리 버전 변경뿐 아니라 사용 방식의 중복 비용을 개선했다.

| 영역 / 라이브러리 | 판단 및 반영 |
| --- | --- |
| pyserial-asyncio | serialx 1.11.0으로 변경. 사용 중인 HA 2026.10.2와 버전을 맞춤. 실제 가상 시리얼 포트 검증 |
| asyncio TCP | 기존 StreamReader/Writer 유지. 시리얼 라이브러리 변경은 EW11 TCP 경로를 가속하지 않음 |
| asyncio timeout | 수신 범위를 명시. Python 3.12+ wait_for도 timeout을 사용하므로 Task 제거에 따른 성능 향상은 주장하지 않음 |
| asyncio 송신 대기 | 20ms마다 확인하던 유휴 대기를 남은 유휴 시간에 맞춤. 200ms 유휴 조건과 1초 대기 한계 유지 |
| 패킷 해석 | 매 속성 접근마다 반복한 주소 해석, payload bytes 슬라이스, 타입 조회를 패킷당 한 번으로 축소 |
| dataclasses | DeviceKey/DeviceState에 slots 사용. 상태의 동적 필드 2개를 명시해 인스턴스 사전 비용 제거 |
| logging | DEBUG가 꺼져 있을 때 정상 수신 패킷의 hex 문자열 생성 생략. 동적 디버그 활성화 지원 유지 |
| HA dispatcher | 아직 발견되지 않은 OFF 채널에 구독자 없는 업데이트 발행 생략. 실제 장치의 동일 상태 응답도 명령 확인에는 계속 전달 |
| HA RestoreEntity | 모든 엔티티마다 전체 게이트웨이 힌트를 복제 저장하던 것을 해당 장치 힌트로 제한. 리스트 복사로 저장 스냅샷의 외부 변경 방지 |
| HA device registry | 게이트웨이 등록 후 최신 via_device_id 연결. 구버전 API 분기. 기존 엔티티/기기 식별자 유지 |
| 플랫폼 속성 / polling | 메모리 상태를 읽는 push 방식 유지. 주기적 네트워크 조회 추가 없음 |
| voluptuous / JSON 저장 | 설정 화면 검증은 빈도가 낮고 상태 JSON 저장은 HA 소유. 별도 대체 라이브러리 추가의 이득 근거 없음 |

## 비교 측정

동일 실행 환경 Python 3.12에서 2.0.6(`bcc88eb`)과 수정본을 비교했다.
조명 상태 변화, 난방, 환기, 엘리베이터, 공기질 6종의 유효한 패킷을
5,000회 반복해 총 30,000프레임을 처리했다. 각 버전 5회 측정의 중앙값이다.

| 버전 | 30,000프레임 처리 시간 |
| --- | ---: |
| 2.0.6 | 0.7384초 |
| 2.0.7 수정본 | 0.6097초 |

이 실행에서는 처리 시간 약 17.4% 감소(처리량 약 21.1% 증가).
16개 최종 엔티티의 상태·속성 결과가 동일함을 비교했다.
HA 경계는 tests/support.py의 대역이며, 실제 HA의 CPU/DB 부하나
월패드 반응 속도를 측정한 값은 아니다. 로그 출력은 비활성화되어 있다.
반복 패킷에도 상태 변화를 포함하지만 전체 모델/명령 조합을 대표하지는 않는다.

재현:

```bash
python scripts/benchmark_protocol.py --repo /path/to/2.0.6
python scripts/benchmark_protocol.py
python scripts/benchmark_protocol.py --profile
```

벤치마크는 파싱 오류 또는 엔티티 누락 시 실패하도록 구성했다.

## 추가 후보와 이번에 적용하지 않은 이유

- uvloop 등 이벤트 루프 교체: HA 전체가 소유한 실행 환경을 통합 하나가 바꾸지 않는다.
- orjson 등 JSON 교체: 통합 자체의 대량 JSON 처리 경로가 없고, HA 저장 계층을 우회할 이유가 없다.
- NumPy/C 확장/별도 파서: 21바이트 고정 프레임에는 배포/변환 비용이 추가된다. 먼저 반복 해석 제거 효과를 측정했다.
- 스레드·프로세스 분산: 작은 패킷의 큐 전달·동기화 비용과 명령 순서 위험이 있다. 현재 프로파일에서 도입 근거가 없다.
- 중복 수신 패킷 무조건 제거: 같은 상태 응답도 명령 ACK와 재연결 후 가용성 복구에 필요하므로 유지한다.
- HA 상태 변경 합치기/debounce: 빠른 조명·동작 센서 상태 전이를 놓칠 수 있어 적용하지 않았다.
- 유휴 간격/명령 확인 타임아웃 축소: RS485 실측 없이 줄이면 충돌·재전송이 늘 수 있다.
- 자체 영구 저장소 하나로 완전 통합: 더 큰 저장량 감소 후보지만 별도 데이터 마이그레이션이 필요하다. 이번에는 기존 restore 형식과 호환되는 축소만 적용했다.
- 전역 캐시/lru_cache 확대: 주소·미등록 채널이 늘 때 메모리 상한과 무효화 정책이 필요하다. 패킷 단위 필드 계산과 slots부터 적용했다.

## 검증과 운영 확인

회귀 테스트, HA 2025.2.2/Python 3.13과 HA 2026.10.2/Python 3.14의 실제
엔티티/기기 레지스트리 등록, 상태 갱신, 복원 힌트 분리, unavailable, 정리를 검증한다.
Linux pseudo-terminal과 실제 serialx로 송수신, 조용한 구간의 타임아웃, 종료/재접속을 검증한다.
실물 USB 시리얼/EW11 장시간 운전과 속도 향상률은 미검증이다.

운영 시 별도 코콤 애드온과 같은 EW11에 중복 연결하는 프로그램이 있는지 확인한다.
중복 연결이 있다면 코드 미세 최적화보다 그 정리가 중요할 수 있다.
주기적 재시작 자동화가 있으면 장시간 안정성 검증 시 영향을 고려한다.

참고:
- https://contributors.home-assistant.io/blog/2026/04/27/pyserial-to-serialx/
- https://github.com/puddly/serialx/blob/dev/docs/how-to/pyserial-migration.md
- https://developers.home-assistant.io/blog/2026/08/24/device-registry-follow-up-changes/
- https://docs.python.org/3.12/library/asyncio-task.html
- https://docs.python.org/3.12/library/dataclasses.html
- https://docs.python.org/3/howto/logging.html#optimization
