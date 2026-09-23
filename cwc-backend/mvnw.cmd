@REM Licensed to the Apache Software Foundation (ASF) under one or more
@REM contributor license agreements. See the NOTICE file for details.
@REM Apache Maven Wrapper startup batch script, version 3.3.2
@REM https://maven.apache.org/wrapper/

@IF "%__MVNW_ARG0_NAME__%"=="" (SET __MVNW_ARG0_NAME__=%~nx0)
@SET __MVNW_CMD__=
@SET __MVNW_ERROR__=
@SET __MVNW_MAVEN_HOME__=%USERPROFILE%\.m2\wrapper\dists\apache-maven-3.9.9-bin

@IF EXIST "%__MVNW_MAVEN_HOME__%\bin\mvn.cmd" (
  SET __MVNW_CMD__="%__MVNW_MAVEN_HOME__%\bin\mvn.cmd" %*
  GOTO executeCommand
)

@ECHO Downloading Apache Maven 3.9.9 ...
@ECHO (this happens only once, on first run)
@powershell -Command "& { $url='https://repo.maven.apache.org/maven2/org/apache/maven/apache-maven/3.9.9/apache-maven-3.9.9-bin.zip'; $dest='%TEMP%\apache-maven-3.9.9-bin.zip'; $target='%USERPROFILE%\.m2\wrapper\dists\apache-maven-3.9.9-bin'; if (!(Test-Path $target)) { Invoke-WebRequest $url -OutFile $dest; Expand-Archive $dest -DestinationPath '%USERPROFILE%\.m2\wrapper\dists'; Remove-Item $dest; Rename-Item '%USERPROFILE%\.m2\wrapper\dists\apache-maven-3.9.9' $target } }"

@IF ERRORLEVEL 1 GOTO error

@SET __MVNW_CMD__="%__MVNW_MAVEN_HOME__%\bin\mvn.cmd" %*

:executeCommand
@%__MVNW_CMD__%
@GOTO end

:error
@ECHO ERROR: Maven download failed. Please install Apache Maven 3.9+ and add it to your PATH.
@EXIT /B 1

:end
@SET __MVNW_CMD__=
@SET __MVNW_MAVEN_HOME__=
