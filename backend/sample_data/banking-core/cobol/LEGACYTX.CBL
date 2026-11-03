       IDENTIFICATION DIVISION.
       PROGRAM-ID. LEGACYTX.
      *================================================================
      * PURPOSE: OLD BRANCH TELEX EXTRACT. SUPERSEDED IN 2009 BY THE
      *          BRANCH DIRECTORY SERVICE AND NO LONGER SCHEDULED.
      *================================================================
       ENVIRONMENT DIVISION.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
           EXEC SQL INCLUDE SQLCA END-EXEC.
       01  WS-BRANCH-CODE          PIC X(6) VALUE 'LON001'.
       01  WS-BRANCH-NAME          PIC X(40).
       01  WS-TELEX                PIC X(20).
       PROCEDURE DIVISION.
       0000-MAIN.
           EXEC SQL
               SELECT BRANCH_NAME, TELEX_NUMBER
                 INTO :WS-BRANCH-NAME, :WS-TELEX
                 FROM BRANCH_LEGACY
                WHERE BRANCH_CODE = :WS-BRANCH-CODE
           END-EXEC
           DISPLAY WS-BRANCH-NAME ' ' WS-TELEX
           STOP RUN.
