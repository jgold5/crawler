package com.globexbank.qa.steps;

import com.globexbank.qa.pages.PageRegistry;
import io.cucumber.java.en.When;

public class GenericSteps {
    private final PageRegistry registry = new PageRegistry();

    @When("user clicks the {string} button")
    public void userClicksButton(String name) {
        registry.getLocator("current", name, java.util.Map.of());
    }

    @When("user enters {string} in the {string} field")
    public void userEntersInField(String value, String name) {
        registry.getLocator("current", name, java.util.Map.of());
    }
}
