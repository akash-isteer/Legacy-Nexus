       IDENTIFICATION DIVISION.
       PROGRAM-ID. PAYMENT.
      *================================================================
      * PURPOSE: POSTS THE DAILY PAYMENT FILE. EACH PAYMENT IS
      *          VALIDATED, APPLIED TO ITS ACCOUNT AND RECORDED IN
      *          THE PAYMENT LEDGER.
      *================================================================
       ENVIRONMENT DIVISION.
       INPUT-OUTPUT SECTION.
       FILE-CONTROL.
           SELECT PAY-IN-FILE ASSIGN TO PAYIN.
       DATA DIVISION.
       FILE SECTION.
       FD  PAY-IN-FILE.
       01  PAY-IN-RECORD.
           05 PI-PAY-ID            PIC X(16).
           05 PI-ACCT-ID           PIC X(12).
           05 PI-AMOUNT            PIC S9(9)V99.
       WORKING-STORAGE SECTION.
           EXEC SQL INCLUDE SQLCA END-EXEC.
       COPY PAYREC.
       01  WS-RESULT-CODE          PIC X(2).
       01  WS-EOF                  PIC X VALUE 'N'.
           88 END-OF-PAYMENTS      VALUE 'Y'.
       01  WS-REJECT-REASON        PIC X(40).
       01  WS-MAX-PAYMENT          PIC S9(9)V99 VALUE 250000.00.
       01  WS-PAY-COUNT            PIC 9(7) VALUE 0.
       PROCEDURE DIVISION.
       0000-MAIN.
           OPEN INPUT PAY-IN-FILE
           PERFORM 1000-READ-PAYMENT
           PERFORM 2000-PROCESS-PAYMENT UNTIL END-OF-PAYMENTS
           CLOSE PAY-IN-FILE
           STOP RUN.
       1000-READ-PAYMENT.
           READ PAY-IN-FILE
               AT END SET END-OF-PAYMENTS TO TRUE
           END-READ.
       2000-PROCESS-PAYMENT.
           MOVE PI-PAY-ID TO WS-PAY-ID
           MOVE PI-ACCT-ID TO WS-ACCT-ID
           MOVE PI-AMOUNT TO WS-PAY-AMOUNT
           PERFORM 2100-VALIDATE-PAYMENT
           IF WS-RESULT-CODE = '00'
               CALL 'ACCOUNT' USING WS-ACCT-ID WS-PAY-AMOUNT
                                    WS-MAIL-ADDRESS WS-RESULT-CODE
               PERFORM 3000-RECORD-PAYMENT
           END-IF
           PERFORM 1000-READ-PAYMENT.
       2100-VALIDATE-PAYMENT.
           MOVE '00' TO WS-RESULT-CODE
      *    A PAYMENT MUST BE FOR A POSITIVE AMOUNT.
           IF WS-PAY-AMOUNT NOT > ZERO
               MOVE 'AMOUNT NOT POSITIVE - REJECTED' TO WS-REJECT-REASON
               MOVE '30' TO WS-RESULT-CODE
           END-IF
      *    LARGE PAYMENTS ARE HELD FOR MANUAL REVIEW.
           IF WS-PAY-AMOUNT > WS-MAX-PAYMENT
               MOVE 'ABOVE LIMIT - HOLD FOR REVIEW' TO WS-REJECT-REASON
               MOVE '40' TO WS-RESULT-CODE
           END-IF.
       3000-RECORD-PAYMENT.
           EXEC SQL
               INSERT INTO PAYMENT_LEDGER
                      (PAYMENT_ID, ACCOUNT_ID, AMOUNT, MAIL_ADDRESS,
                       POSTED_DATE)
               VALUES (:WS-PAY-ID, :WS-ACCT-ID, :WS-PAY-AMOUNT,
                       :WS-MAIL-ADDRESS, CURRENT DATE)
           END-EXEC
           ADD 1 TO WS-PAY-COUNT.
