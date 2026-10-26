package com.globexbank.qa.steps;

import com.globexbank.qa.pages.GlobexLoginPage;
import com.globexbank.qa.pages.WireTransferPage;
import com.globexbank.qa.grid.LedgerGridHelper;
import io.cucumber.java.en.Given;
import io.cucumber.java.en.When;
import io.cucumber.java.en.Then;
import org.openqa.selenium.By;
import org.openqa.selenium.WebDriver;
import org.openqa.selenium.support.ui.Select;
import static org.junit.jupiter.api.Assertions.assertEquals;

public class TransferSteps {
    private final WebDriver driver = Hooks.driver();

    @Given("^Globex customer \"([^\"]*)\" is logged in$")
    public void customerLoggedIn(String customer) {
        new GlobexLoginPage(driver).loginAs(customer, "Gl0bex!Spring2026");
    }

    @When("the user enters {string} into the {string} field")
    public void enterIntoField(String value, String field) {
        driver.findElement(By.xpath("//input[@aria-label='" + field + "']")).sendKeys(value);
    }

    @When("the user clicks {string}")
    public void clickButton(String label) {
        driver.findElement(By.xpath("//button[normalize-space()='" + label + "']")).click();
    }

    @When("the user picks beneficiary {string}")
    public void pickBeneficiary(String name) {
        new Select(driver.findElement(WireTransferPage.BENEFICIARY_DROPDOWN)).selectByVisibleText(name);
    }

    @When("the user sets {string} to {string} for account {string} in the ledger")
    public void setLedgerCell(String column, String value, String account) {
        var cell = driver.findElement(new LedgerGridHelper(driver).cell(account, column));
        cell.click();
        cell.sendKeys(value);
    }

    @Then("the available balance should be {string}")
    public void balanceShouldBe(String expected) {
        assertEquals(expected, driver.findElement(By.xpath("//span[@data-qa='avail-balance']")).getText());
    }
}
