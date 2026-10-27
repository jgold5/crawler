package com.example.qa.pages;

import static org.junit.Assert.*;

public class HomePage {
    private LedgerReconciliationService svc;
    public void go() { svc.run(); BrokerageSweepEngine.rebalanceMarginAccounts(); }
}
