# 배포 절차

정식 배포는 사용자가 요청했을 때만 진행한다. 아래 예시는 저장소 루트 `D:\Study\cds_exe_patcher`에서 PowerShell로 실행하는 기준이다.

## 1. 배포 전 확인

1. `git status --short`로 의도하지 않은 변경이나 생성 파일이 없는지 확인한다.
2. [변경 이력](../CHANGELOG.md)에 새 버전 항목을 추가한다.
3. `Resources/data/app_config.json`의 `version`을 배포 버전과 맞춘다.
4. 프로그램 제목, 릴리스 태그, ZIP 파일 이름이 모두 같은 버전인지 확인한다.

자동 업데이트가 동작하려면 설정의 저장소, 자산 이름 형식과 실행 파일 이름도 실제 GitHub Release와 정확히 일치해야 한다.

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

## 2. 정적 검사

루트 파일과 `Resources/py`의 Python 소스를 모두 컴파일 검사한다. PowerShell의 와일드카드를 `py_compile`에 그대로 넘기지 않고 파일 목록을 펼쳐 전달한다.

```powershell
$sources = @('.\CDSExecutablePatcher.pyw') + @(Get-ChildItem '.\Resources\py' -Filter '*.py' | ForEach-Object FullName)
py -3.14 -m py_compile $sources
```

문서와 패치 형식 오류도 확인한다.

```powershell
git diff --check
```

## 3. 실행 파일 빌드

```powershell
py -3.14 -m PyInstaller --noconfirm .\CDS_EXE_Patcher.spec
```

다음을 확인한다.

- `dist\CDS_EXE_Patcher.exe`가 생성되었는가
- 아이콘과 스플래시가 정상인가
- 시작 직후 오류 없이 메인 창이 중앙에 표시되는가
- 게임 EXE 선택 후 각 탭과 원본 리소스 미리보기가 열리는가
- 새 버전이 없을 때 업데이트 설치 버튼이 숨겨지는가

## 4. ZIP과 해시 생성

버전 변수 하나를 사용해 설정, 태그와 파일 이름이 어긋나는 실수를 줄인다.

```powershell
$releaseVersion = '1.3.0'
$zipPath = ".\dist\CDS_EXE_Patcher_v$releaseVersion.zip"
Compress-Archive -LiteralPath '.\dist\CDS_EXE_Patcher.exe' -DestinationPath $zipPath -CompressionLevel Optimal -Force
Get-FileHash -Algorithm SHA256 -LiteralPath '.\dist\CDS_EXE_Patcher.exe', $zipPath
```

ZIP의 루트에는 `CDS_EXE_Patcher.exe`가 있어야 한다. 자동 업데이터는 다른 디렉터리 단계나 다른 실행 파일 이름을 전제로 하지 않는다.

## 5. 커밋, 태그와 GitHub Release

1. 최종 `git diff`와 `git status`를 확인한다.
2. 버전 변경과 문서를 포함해 커밋한다.
3. 태그 이름은 `1.3.0` 또는 저장소에서 계속 사용한 동일한 형식으로 만든다.
4. 커밋과 태그를 원격 저장소에 푸시한다.
5. 같은 태그로 정식 GitHub Release를 만들고 `CDS_EXE_Patcher_v1.3.0.zip`을 첨부한다.
6. Release 본문에는 [변경 이력](../CHANGELOG.md)의 해당 버전 내용을 사용한다.

초안 Release와 사전 배포는 자동 업데이트 대상에서 제외될 수 있으므로 정식 Release로 게시한다.

## 6. 배포 후 검증

- GitHub Releases API에서 최신 정식 태그와 ZIP 자산 이름을 확인한다.
- 이전 버전 EXE를 실행해 새 버전 설치 버튼이 나타나는지 확인한다.
- 업데이트 후 새 EXE가 실행되고 v1.0.0부터 새 버전까지 누적 릴리스 내역이 표시되는지 확인한다.
- GitHub의 CDN·API 반영에는 짧은 지연이 생길 수 있으므로 게시 직후 보이지 않으면 잠시 뒤 다시 확인한다.

업데이트 버튼이 나타나지 않으면 버전 비교보다 먼저 다음 네 항목을 확인한다.

1. 현재 앱 버전보다 Release 태그가 높은가
2. Release가 초안이나 사전 배포가 아닌가
3. ZIP 자산 이름이 `asset_name` 형식과 정확히 같은가
4. ZIP 안 실행 파일 이름이 `executable_name`과 같은가

