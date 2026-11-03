       IDENTIFICATION DIVISION.
       PROGRAM-ID. CUSTOMER.
      *================================================================
      * PURPOSE: RETURNS A CUSTOMER MAILING ADDRESS AND STATUS FROM
      *          CUSTOMER_MASTER. CALLED BY ACCOUNT DURING PAYMENT
      *          POSTING AND BY THE CUSTOMER LOOKUP API.
      *================================================================
       ENVIRONMENT DIVISION.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
           EXEC SQL INCLUDE SQLCA END-EXEC.
       LINKAGE SECTION.
       01  LK-CUST-ID              PIC X(10).
       01  LK-CUST-ADDRESS         PIC X(80).
       01  LK-CUST-STATUS          PIC X(1).
           88 LK-CUST-ACTIVE       VALUE 'A'.
           88 LK-CUST-CLOSED       VALUE 'C'.
           88 LK-CUST-NOT-FOUND    VALUE 'X'.
       PROCEDURE DIVISION USING LK-CUST-ID LK-CUST-ADDRESS
                                LK-CUST-STATUS.
       0000-MAIN.
           PERFORM 1000-GET-CUSTOMER
           GOBACK.
       1000-GET-CUSTOMER.
           EXEC SQL
               SELECT CUSTOMER_ADDRESS, CUSTOMER_STATUS
                 INTO :LK-CUST-ADDRESS, :LK-CUST-STATUS
                 FROM CUSTOMER_MASTER
                WHERE CUSTOMER_ID = :LK-CUST-ID
           END-EXEC
           IF SQLCODE NOT = 0
               MOVE 'X' TO LK-CUST-STATUS
               MOVE SPACES TO LK-CUST-ADDRESS
           END-IF.
