# 대항해시대 III EXE 패처

대항해시대 III의 실행 파일 설정을 안전하게 읽고 수정하는 Windows용 GUI 패처입니다.
원본 EXE는 수정 전에 백업하며, 지원하지 않는 파일이나 예상하지 못한 바이트는 적용하지 않습니다.

## 기능

- 시작 해상도와 전체화면 허용 크기 설정
- 좌표 표시 형식 변경
- NPC 이동·활동, 게임 진행, 위도 및 전투 인카운트 설정
- 명성 단계별 해적 함대 출현 설정
- 오역 수정 및 일식 관측 영역 패치

## 실행

개발 환경에서는 Python 3.14 이상에서 다음 파일을 실행합니다.

```powershell
py -3.14 .\CDSExecutablePatcher.pyw
```

배포본은 PyInstaller 설정 파일을 사용해 빌드합니다.

```powershell
py -3.14 -m PyInstaller --noconfirm .\CDS_EXE_Patcher.spec
```

## 자동 업데이트 기반

GitHub Releases를 이용한 자동 업데이트 기반을 포함합니다. 현재
[`Resources/data/app_config.json`](Resources/data/app_config.json)의
`update.repository`가 비어 있으므로 업데이트 확인과 다운로드는 비활성 상태입니다.
배포 저장소를 정한 뒤 해당 값을 설정하고 GUI 동작을 연결하면, 릴리스 ZIP을 내려받아
검증·교체·재시작하는 흐름을 사용할 수 있습니다.

## 주의

패치 대상 EXE가 실행 중이지 않은지 확인하고, 자동 생성된 `.bak` 백업 파일은 보관하세요.
이 도구는 지원하는 대항해시대 III EXE 형식에만 사용해야 합니다.
