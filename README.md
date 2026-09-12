# 대항해시대 III EXE 패처

대항해시대 III 한국어판의 `CDS_95.EXE` 설정과 정적 마스터 데이터를 읽고 수정하는 Windows용 GUI 패처입니다. 지원하지 않는 실행 파일이나 예상과 다른 바이트 배열은 쓰기를 거부하며, 실제 변경이 있을 때 원본 파일의 백업을 한 번만 생성합니다.

## 주요 기능

### 기본 설정

- 시작 해상도 3종과 전체화면 허용 크기 설정
- 좌표 표시 형식 변경
- 장기 휴양, 탐험 준비, 세대교체 및 인물 활동 가능 나이 설정
- 소지금, 저금, 명성, 악명 상한 설정
- 일반 NPC 이동 확률과 도착 대기 기간 설정
- 북·남쪽 추위 전멸 경계와 일식 관측 위도 설정
- 서부·동부 해역 전투 인카운트 빈도 설정
- 명성 단계별 해적 함대 후보와 출현 확률 설정

### 추가 패치

- 용어·지명·아이템·인명·힌트 오역 수정
- 카바신전 발견물, 정지 이미지와 이벤트 주입
- 노예 도서관 힌트와 발견 대사 추가
- 무제국 힌트 획득 후 발견 가능하도록 조건 수정
- 카바신전·노예 발견물의 `SAVEDATA.CDS` 상태 보정
- 체크 해제 시 패치 데이터 복원

### 정적 마스터 편집

- 여급: 이름, 얼굴, 출현 연도·도시, 성격, 전수 언어, 얼굴별 자녀 능력치 보정
- 후원자: 얼굴, 성별, 국가, 직업, 등장 연도, 소재지, 권력·재산·평가 계수, 취향과 언어
- 인물: 기본 정보, 등용 상태, 생명력, 능력치, 기술과 언어
- 함선: 선종 이름, 조선소 조건, 추진력·내구력·적재량·대포·승무원 수치
- 도시: 기본 정보, 조선소, 시장, 교역, 시설과 내륙 연결
- 교역권: 교역권별 공통 교역품 5종
- 아이템: 이름, 분류, 매입가·매각가, 효과 코드
- 발견물: 이름, 분류, 가치, 발견 좌표 범위와 미디어 연결
- 선수상 효과: 재해 방지 확률, 피해 감소율, 공격 배율, 내구 회복과 이동력

### 원본 미디어 미리보기

선택한 EXE와 같은 폴더의 게임 리소스를 실행 중에 직접 읽습니다.

| 파일 | 사용처 |
|---|---|
| `FEMALE.CDS`, `MALE.CDS` | 여급·후원자·인물 얼굴 |
| `ITEM.CDS` | 아이템과 교역품 이미지 |
| `CITYCG.CDS` | 도시 이미지 |
| `DSTILL.CDS` | 발견물 정지 이미지 |
| `DISCOVER.CDS` | 발견 연출 애니메이션 |
| `AVI` 폴더 | 함선 및 발견물 동영상 |

이미지는 가능한 한 원본 크기로 표시합니다. AVI와 `DISCOVER.CDS` 애니메이션 재생에는 배포본에 포함된 VLC 런타임을 사용합니다.

## 실행

Python 3.14 이상과 프로젝트 의존성이 설치된 개발 환경에서 실행합니다.

```powershell
py -3.14 .\CDSExecutablePatcher.pyw
```

## 배포 빌드

```powershell
py -3.14 -m PyInstaller --noconfirm .\CDS_EXE_Patcher.spec
$releaseVersion = (Get-Content -LiteralPath '.\Resources\data\app_config.json' -Raw | ConvertFrom-Json).version
Compress-Archive -LiteralPath .\dist\CDS_EXE_Patcher.exe `
  -DestinationPath ".\dist\CDS_EXE_Patcher_v$releaseVersion.zip" -CompressionLevel Optimal -Force
```

ZIP 이름의 버전은 `Resources/data/app_config.json`의 버전과 일치해야 합니다. 자세한 절차는 [배포 절차](docs/RELEASE.md)를 참고하세요.

## 자동 업데이트

배포 EXE는 시작 시 GitHub의 최신 정식 Release를 비동기로 확인합니다. 새 버전과 정확한 ZIP 자산이 확인될 때만 업데이트 설치 버튼을 표시합니다. 업데이트를 마치고 재시작하면 `v1.0.0`부터 설치된 최신 버전까지의 정식 릴리스 내역을 스크롤 창으로 보여 줍니다.

설정 위치는 `Resources/data/app_config.json`입니다.

```json
{
  "version": "1.3.0",
  "update": {
    "repository": "dkenldlqfur/cds_exe_patcher",
    "asset_name": "CDS_EXE_Patcher_v{version}.zip",
    "executable_name": "CDS_EXE_Patcher.exe"
  }
}
```

## 안전성

- 패치 전 대상 파일과 원본 명령·레코드 형식을 검증합니다.
- 이름처럼 원래 공간보다 길어질 수 있는 데이터는 `.patch` 섹션의 독립 슬롯에 저장합니다.
- 기능별 슬롯과 식별자를 사용해 기존 패치와 충돌하는 데이터를 감지합니다.
- 변경 사항을 메모리에서 조립한 뒤 임시 파일과 원자적 교체 방식으로 저장합니다.
- 이미 적용된 값을 다시 저장할 때 불필요한 파일 변경을 만들지 않습니다.
- 패치 대상 EXE가 실행 중이지 않은 상태에서 사용해야 합니다.

## 개발 문서

- [변경 이력](CHANGELOG.md)
- [배포 절차](docs/RELEASE.md)
- 게임 파일과 EXE 구조 분석: `D:\OldGames\Games\CDS95K_Win_220518\docs`
