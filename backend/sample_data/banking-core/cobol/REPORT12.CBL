       IDENTIFICATION DIVISION.
       PROGRAM-ID. REPORT12.
      *================================================================
      * PURPOSE: PRODUCES REPORT 12, THE DAILY POSTINGS STATEMENT THAT
      *          LISTS EVERY PAYMENT WITH ITS MAILING ADDRESS.
      *================================================================
       ENVIRONMENT DIVISION.
       INPUT-OUTPUT SECTION.
       FILE-CONTROL.
           SELECT RPT-FILE ASSIGN TO RPTOUT.
       DATA DIVISION.
       FILE SECTION.
       FD  RPT-FILE.
       01  RPT-RECORD.
           05 RPT-PAY-ID           PIC X(16).
           05 RPT-AMOUNT           PIC -Z(8)9.99.
           05 RPT-ADDRESS          PIC X(80).
       WORKING-STORAGE SECTION.
           EXEC SQL INCLUDE SQLCA END-EXEC.
       01  WS-PAY-ID               PIC X(16).
       01  WS-AMOUNT               PIC S9(9)V99.
       01  WS-ADDRESS              PIC X(80).
       01  WS-LINE-COUNT           PIC 9(5) VALUE 0.
           EXEC SQL
               DECLARE PAYCUR CURSOR FOR
                   SELECT PAYMENT_ID, AMOUNT, MAIL_ADDRESS
                     FROM PAYMENT_LEDGER
                    WHERE POSTED_DATE = CURRENT DATE
           END-EXEC.
       PROCEDURE DIVISION.
       0000-MAIN.
           OPEN OUTPUT RPT-FILE
           EXEC SQL OPEN PAYCUR END-EXEC
           PERFORM 1000-FETCH-PAYMENT
           PERFORM 2000-WRITE-LINE UNTIL SQLCODE NOT = 0
           EXEC SQL CLOSE PAYCUR END-EXEC
           CLOSE RPT-FILE
           STOP RUN.
       1000-FETCH-PAYMENT.
           EXEC SQL
               FETCH PAYCUR INTO :WS-PAY-ID, :WS-AMOUNT, :WS-ADDRESS
           END-EXEC.
       2000-WRITE-LINE.
           MOVE WS-PAY-ID TO RPT-PAY-ID
           MOVE WS-AMOUNT TO RPT-AMOUNT
           MOVE WS-ADDRESS TO RPT-ADDRESS
           WRITE RPT-RECORD
           ADD 1 TO WS-LINE-COUNT
           PERFORM 1000-FETCH-PAYMENT.
