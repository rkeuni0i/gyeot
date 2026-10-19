# Windows 기본 OCR(Windows.Media.Ocr, 한국어)로 그림 파일의 글자를 읽는다. 설치할 것 없음(Windows 10/11 + 한국어 OCR).
# 사용: powershell -NoProfile -ExecutionPolicy Bypass -File ocr_win.ps1 <입력 폴더> <출력 JSON> [언어=ko]
# 입력 폴더의 *.png를 이름 순서로 읽어 {"파일이름": "줄1\n줄2..."} JSON(UTF-8)으로 저장한다.
param([string]$InDir, [string]$OutJson, [string]$Lang = "ko")
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$type) {
    $t = $asTaskGeneric.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(-1) | Out-Null; $t.Result
}
[Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime] | Out-Null
[Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime] | Out-Null
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage((New-Object Windows.Globalization.Language $Lang))
if ($null -eq $engine) { throw "이 PC에 '$Lang' OCR 언어가 없습니다(설정 > 시간 및 언어 > 언어에서 한국어 추가)." }
$out = [ordered]@{}
foreach ($f in (Get-ChildItem -Path $InDir -Filter *.png | Sort-Object Name)) {
    $sf = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($f.FullName)) ([Windows.Storage.StorageFile])
    $stream = Await ($sf.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bmp = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $res = Await ($engine.RecognizeAsync($bmp)) ([Windows.Media.Ocr.OcrResult])
    $out[$f.Name] = (($res.Lines | ForEach-Object { $_.Text }) -join "`n")
    $stream.Dispose()
}
[System.IO.File]::WriteAllText($OutJson, ($out | ConvertTo-Json -Depth 3), (New-Object System.Text.UTF8Encoding($false)))
