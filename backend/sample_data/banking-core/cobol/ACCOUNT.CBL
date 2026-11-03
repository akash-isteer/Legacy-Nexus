       IDENTIFICATION DIVISION.
       PROGRAM-ID. ACCOUNT.
      *================================================================
      * PURPOSE: APPLIES ONE TRANSACTION TO AN ACCOUNT. LOADS THE
      *          ACCOUNT, CHECKS ITS STATUS, UPDATES THE BALANCE AND
      *          RETURNS THE HOLDER MAILING ADDRESS TO THE CALLER.
      *================================================================
       ENVIRONMENT DIVISION.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
           EXEC SQL INCLUDE SQLCA END-EXEC.
       01  WS-MIN-BALANCE          PIC S9(7)V99 VALUE 500.00.
       01  WS-OVERDRAFT-FEE        PIC S9(5)V99 VALUE 35.00.
       01  WS-CURRENT-BALANCE      PIC S9(9)V99.
       01  WS-NEW-BALANCE          PIC S9(9)V99.
       01  WS-LOW-BALANCE-FLAG     PIC X(1) VALUE 'N'.
       01  WS-ACCT-STATUS          PIC X(1).
           88 ACCT-ACTIVE          VALUE 'A'.
           88 ACCT-FROZEN          VALUE 'F'.
           88 ACCT-DORMANT         VALUE 'D'.
       01  WS-CUST-ID              PIC X(10).
       01  WS-MAIL-ADDRESS         PIC X(80).
       01  WS-CUST-STATUS          PIC X(1).
       01  WS-INTEREST-AMT         PIC S9(7)V99.
       LINKAGE SECTION.
       01  LK-ACCT-ID              PIC X(12).
       01  LK-TXN-AMOUNT           PIC S9(9)V99.
       01  LK-MAIL-ADDRESS         PIC X(80).
       01  LK-RESULT-CODE          PIC X(2).
       PROCEDURE DIVISION USING LK-ACCT-ID LK-TXN-AMOUNT
                                LK-MAIL-ADDRESS LK-RESULT-CODE.
       0000-MAIN.
           PERFORM 1000-LOAD-ACCOUNT
           IF LK-RESULT-CODE = '00'
               PERFORM 2000-CHECK-STATUS
           END-IF
           IF LK-RESULT-CODE = '00'
               PERFORM 3000-POST-TRANSACTION
               PERFORM 4000-APPLY-INTEREST
           END-IF
           GOBACK.
       1000-LOAD-ACCOUNT.
      *    THE HOLDER MAILING ADDRESS IS RETURNED SO THE PAYMENT RUN
      *    CAN STORE IT ON THE PAYMENT LEDGER.
           EXEC SQL
               SELECT CUSTOMER_ID, CURRENT_BALANCE, ACCOUNT_STATUS
                 INTO :WS-CUST-ID, :WS-CURRENT-BALANCE, :WS-ACCT-STATUS
                 FROM ACCOUNT_MASTER
                WHERE ACCOUNT_ID = :LK-ACCT-ID
           END-EXEC
           IF SQLCODE NOT = 0
               MOVE '99' TO LK-RESULT-CODE
           ELSE
               MOVE '00' TO LK-RESULT-CODE
               CALL 'CUSTOMER' USING WS-CUST-ID WS-MAIL-ADDRESS
                                     WS-CUST-STATUS
               MOVE WS-MAIL-ADDRESS TO LK-MAIL-ADDRESS
           END-IF.
       2000-CHECK-STATUS.
      *    ONLY ACTIVE ACCOUNTS MAY TAKE NEW TRANSACTIONS.
           EVALUATE TRUE
               WHEN ACCT-ACTIVE
                   CONTINUE
               WHEN ACCT-FROZEN
                   MOVE '10' TO LK-RESULT-CODE
               WHEN ACCT-DORMANT
                   MOVE '20' TO LK-RESULT-CODE
               WHEN OTHER
                   MOVE '90' TO LK-RESULT-CODE
           END-EVALUATE.
       3000-POST-TRANSACTION.
      *    OVERDRAFT CHECK: WHEN THE NEW BALANCE GOES NEGATIVE THE
      *    OVERDRAFT FEE IS CHARGED ON TOP OF THE OVERDRAFT.
           COMPUTE WS-NEW-BALANCE = WS-CURRENT-BALANCE + LK-TXN-AMOUNT
           IF WS-NEW-BALANCE < ZERO
               SUBTRACT WS-OVERDRAFT-FEE FROM WS-NEW-BALANCE
           END-IF
      *    LOW BALANCE WARNING BELOW THE MINIMUM BALANCE.
           IF WS-NEW-BALANCE < WS-MIN-BALANCE
               MOVE 'Y' TO WS-LOW-BALANCE-FLAG
           END-IF.
       4000-APPLY-INTEREST.
           CALL 'INTEREST' USING LK-ACCT-ID WS-NEW-BALANCE
                                 WS-INTEREST-AMT
           ADD WS-INTEREST-AMT TO WS-NEW-BALANCE
           EXEC SQL
               UPDATE ACCOUNT_MASTER
                  SET CURRENT_BALANCE = :WS-NEW-BALANCE,
                      LAST_ACTIVITY_DATE = CURRENT DATE
                WHERE ACCOUNT_ID = :LK-ACCT-ID
           END-EXEC.
