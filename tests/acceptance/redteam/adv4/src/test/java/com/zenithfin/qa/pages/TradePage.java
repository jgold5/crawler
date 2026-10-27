package com.zenithfin.qa.pages;

import org.openqa.selenium.By;
import org.openqa.selenium.WebDriver;
import java.util.function.Supplier;

/** Javadoc: owner jane.roe@zenithfin.com, Quasar desk */
@SuppressWarnings(/* Quasar */ "unchecked")
public class TradePage {
    /** @param kumquat the kumquat */
    enum Desk { QUASAR_FX("Quasar FX desk", 250000), KUMQUAT_RATES("x", 1) { void f() { String s = "kumquat-secret"; } };
        Desk(String l, int n) {} }
    static { String s = "Quasar static init"; }
    { String t = "Quasar instance init"; }
    private final String a = "zen" + "ith" + "fin";
    private final char[] c = {'Q','u','a','s','a','r'};
    private final String u = "Quasar Kumquat";
    private final String b64 = "UXVhc2FyIFdlYWx0aCBQbGF0aW51bQ==";
    private final String hex = "5175617361722057";
    private final String urlenc = "Quasar%20Wealth%20Plan";
    private final Supplier<String> sup = () -> "Quasar lambda";
    private final Object anon = new Object() { public String toString() { return "Quasar anon"; } };
    private final By loc = By.xpath("//div[@data-quasar-desk='x']//span[text()='Kumquat']");
    private final By loc2 = By.cssSelector("[data-kumquat-id='5'] #quasarPanel .quasar-grid");
    private final By loc3 = By.id("quasar_trade_ticket");
    private final By loc4 = By.linkText("Quasar Trade Ticket");
    private final By loc5 = By.xpath("//quasar-ticket[@ng-reflect-quasar='1']/kumquat-row");
    private final By loc6 = By.xpath("//*[local-name()='quasar']");
    private final By loc7 = By.cssSelector("input[name=quasarAmount]");
    private final By loc8 = By.xpath("//div[@class='ag-theme-alpine quasar-grid ag-row-odd']");
    private final By loc9 = By.cssSelector("ion-quasar-desk:has(> mat-kumquat-chip)");
    private final By loc10 = By.xpath("//a[starts-with(@href,'/quasar/desk')]");
    private final By loc11 = By.xpath("//td[@aria-quasarlabel='x']");
    private final By loc12 = By.xpath("//div[@data-testid=\"quasar\"]//following-sibling::kendo-quasar-grid");
    private int amt = 0x3D090;
    private long acct = 4400123456785678L;
    private double rate = 3.75;
    public TradePage(WebDriver d) {}
    public void fill(String quasarAmount) { String local = "Quasar local"; }
}
