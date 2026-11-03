       IDENTIFICATION DIVISION.
       PROGRAM-ID. INTEREST.
      *================================================================
      * PURPOSE: CALCULATES DAILY INTEREST ON AN ACCOUNT BALANCE USING
      *          THE ANNUAL RATE HELD ON ACCOUNT_MASTER.
      *----------------------------------------------------------------
      * MODIFICATION HISTORY
      *   1994-03  INITIAL VERSION
      *   1999-11  Y2K REVIEW, NO DATE LOGIC IN THIS MODULE
      *   2006-05  MINIMUM RATE FLOOR ADDED (CHANGE REQUEST 4471)
      *   2014-09  RATE READ MOVED FROM VSAM TO DB2 ACCOUNT_MASTER
      *================================================================
       ENVIRONMENT DIVISION.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
           EXEC SQL INCLUDE SQLCA END-EXEC.
      *----------------------------------------------------------------
      * RATE WORK AREAS
      *----------------------------------------------------------------
       01  WS-RATE                 PIC S9(3)V9(4) COMP-3.
       01  WS-RATE-FLOOR           PIC S9(3)V9(4) VALUE 0.0100.
       01  WS-DAYS-IN-YEAR         PIC 9(3) VALUE 365.
      *----------------------------------------------------------------
      * PARAMETERS FROM THE CALLER
      *----------------------------------------------------------------
       LINKAGE SECTION.
       01  LK-ACCT-ID              PIC X(12).
       01  LK-BALANCE              PIC S9(9)V99.
       01  LK-INTEREST-AMT         PIC S9(7)V99.
       PROCEDURE DIVISION USING LK-ACCT-ID LK-BALANCE
                                LK-INTEREST-AMT.
      *----------------------------------------------------------------
      * MAIN LINE
      *----------------------------------------------------------------
       0000-MAIN.
           MOVE ZERO TO LK-INTEREST-AMT
           PERFORM 1000-GET-RATE
           IF SQLCODE = 0 AND LK-BALANCE > ZERO
               PERFORM 2000-CALCULATE-INTEREST
           END-IF
           GOBACK.
      *----------------------------------------------------------------
      * READ THE ANNUAL INTEREST RATE FOR THE ACCOUNT
      *----------------------------------------------------------------
       1000-GET-RATE.
           EXEC SQL
               SELECT INTEREST_RATE
                 INTO :WS-RATE
                 FROM ACCOUNT_MASTER
                WHERE ACCOUNT_ID = :LK-ACCT-ID
           END-EXEC.
      *----------------------------------------------------------------
      * CALCULATE ONE DAY OF INTEREST
      *----------------------------------------------------------------
       2000-CALCULATE-INTEREST.
      *    RATES BELOW THE FLOOR ARE RAISED TO THE FLOOR RATE.
           IF WS-RATE < WS-RATE-FLOOR
               MOVE WS-RATE-FLOOR TO WS-RATE
           END-IF
      *    DAILY INTEREST = BALANCE * ANNUAL RATE / DAYS IN YEAR.
           COMPUTE LK-INTEREST-AMT ROUNDED =
               LK-BALANCE * WS-RATE / 100
               / WS-DAYS-IN-YEAR
           END-COMPUTE.
