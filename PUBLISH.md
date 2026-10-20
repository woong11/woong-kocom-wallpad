# GitHub / HACS 게시 절차

대상 저장소: `woong11/woong-kocom-wallpad`. 아래는 이후 변경을 게시할 때의 절차입니다.
HACS는 릴리스가 없어도 기본 브랜치에서 설치할 수 있습니다.

1. 대상 저장소의 최신 main을 가져오고 기존 변경 사항을 확인합니다.
2. 저장소 루트에 이 패키지의 파일을 반영합니다. `custom_components`가 루트 바로 아래에 있어야 합니다.
3. 아래 명령으로 manifest의 문서·이슈 링크와 관리자를 자신의 저장소로 설정합니다.

   ```powershell
   python scripts/configure_repository.py 내아이디/kocom-wallpad
   ```

   Python이 없다면 `custom_components/kocom_wallpad/manifest.json`의 `documentation`, `issue_tracker`, `codeowners` 3개 값을 직접 수정할 수 있습니다. 원저자 표시는 README에 남아 있습니다.

4. 변경 사항을 기본 브랜치에 커밋·푸시합니다. 원본의 파일 변경 외에 `tests`, `scripts`, `brand`, `diagnostics.py`, 테스트 workflow도 포함해야 합니다.
5. 선택 사항: GitHub Releases에서 태그 `v2.0.8`, 제목 `2.0.8 - HA 호환성 및 통신 개선`를 생성합니다. 태그는 수정 커밋을 가리켜야 합니다. 변경 설명은 CHANGELOG의 2.0.8 항목을 사용합니다.
6. HACS → 사용자 지정 저장소에 자신의 저장소 URL을 넣고 유형을 Integration으로 지정합니다.
7. 기존 원본과 domain이 같으므로 HACS에서 중복 관리하지 않습니다. HA의 기존 통합 설정 항목은 유지하고, 코드 다운로드 후 HA를 재시작합니다.

HACS는 release가 없는 저장소의 기본 브랜치도 설치할 수 있습니다. 이 패키지는 `zip_release` 방식을 사용하지 않으므로 릴리스에 ZIP을 첨부할 필요는 없습니다.

## 검증 기록

- Python 3.12: unittest 31개 통과.
- Python 3.13 / HA 2025.2.2 및 Python 3.14 / HA 2026.10.2: 전체 모듈 import와 엔티티 테스트 통과.
- 실제 HA EntityPlatform 사용: 8개 엔티티 등록, 상태 dispatcher 갱신, 연결 해제 후 unavailable, 플랫폼 정리 통과.
- 실제 device registry의 게이트웨이 연결 및 serialx 가상 시리얼 송수신·재접속 통과.
- 전체 Python compile 및 git diff whitespace 검증 통과.
- 실물 EW11/RS485/USB 시리얼 및 사용 중인 HA의 HACS 설치는 미검증.

## 남은 작업

기본 브랜치 게시 후 Actions 결과와 실제 HA에서 설치·동작을 확인합니다. GitHub Release는 선택 사항입니다.
