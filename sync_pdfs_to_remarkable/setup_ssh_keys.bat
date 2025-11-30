@echo off
echo ========================================
echo SSH Key Setup for reMarkable Device
echo ========================================
echo.
echo This will set up passwordless SSH access to your reMarkable.
echo You will need to enter your password ONCE during setup.
echo.
pause

set REMARKABLE_HOST=root@192.168.1.226
set SSH_DIR=%USERPROFILE%\.ssh
set KEY_FILE=%SSH_DIR%\id_rsa

REM Create .ssh directory if it doesn't exist
if not exist "%SSH_DIR%" mkdir "%SSH_DIR%"

REM Check if key already exists
if exist "%KEY_FILE%" (
    echo.
    echo SSH key already exists at: %KEY_FILE%
    echo.
    set /p USE_EXISTING="Use existing key? (y/n): "
    if /i not "%USE_EXISTING%"=="y" (
        echo Generating new SSH key...
        ssh-keygen -t rsa -b 4096 -f "%KEY_FILE%" -N ""
    )
) else (
    echo Generating SSH key...
    ssh-keygen -t rsa -b 4096 -f "%KEY_FILE%" -N ""
)

echo.
echo Copying SSH public key to reMarkable device...
echo You will be prompted for your password ONCE.
echo.
echo Command: type "%KEY_FILE%.pub" ^| ssh %REMARKABLE_HOST% "mkdir -p ~/.ssh ^&^& cat >> ~/.ssh/authorized_keys ^&^& chmod 600 ~/.ssh/authorized_keys ^&^& chmod 700 ~/.ssh"
echo.

type "%KEY_FILE%.pub" | ssh %REMARKABLE_HOST% "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && chmod 700 ~/.ssh"

if %ERRORLEVEL% EQU 0 (
    echo.
    echo ========================================
    echo SUCCESS! SSH key authentication is now set up.
    echo ========================================
    echo.
    echo You should now be able to connect without entering a password.
    echo Testing connection...
    echo.
    ssh -o ConnectTimeout=5 %REMARKABLE_HOST% "echo 'SSH key authentication working!'"
    if %ERRORLEVEL% EQU 0 (
        echo.
        echo Connection test successful! Passwordless SSH is working.
    ) else (
        echo.
        echo Connection test failed. Please check your setup.
    )
) else (
    echo.
    echo ========================================
    echo ERROR: Failed to copy SSH key
    echo ========================================
    echo.
    echo You can manually copy the key by running:
    echo   type "%KEY_FILE%.pub"
    echo.
    echo Then SSH to the device and run:
    echo   mkdir -p ~/.ssh
    echo   echo "PASTE_KEY_HERE" >> ~/.ssh/authorized_keys
    echo   chmod 600 ~/.ssh/authorized_keys
    echo   chmod 700 ~/.ssh
)

echo.
pause

