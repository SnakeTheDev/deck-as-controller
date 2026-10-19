param([string]$Filter = 'USB\VID_*')

Get-CimInstance Win32_PnPEntity |
    Where-Object { $_.DeviceID -like $Filter -and $_.DeviceID -notlike '*VID_045E&PID_08*' } |
    Select-Object Name, Status, ConfigManagerErrorCode, Service, DeviceID |
    Format-List