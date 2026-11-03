package com.bank.digital;

import java.math.BigDecimal;
import jakarta.persistence.*;

@Entity
@Table(name = "ACCOUNT_MASTER")
public class Account {

    @Id
    @Column(name = "ACCOUNT_ID")
    private String id;

    @Column(name = "CURRENT_BALANCE")
    private BigDecimal balance;

    private boolean lowBalance;

    public BigDecimal getBalance() {
        return balance;
    }

    public void setLowBalance(boolean lowBalance) {
        this.lowBalance = lowBalance;
    }
}
