package app;

import core.Animal;
import core.AnimalFactory;
import core.Cat;
import core.Config;
import core.Container;
import core.Dog;
import core.Duck;
import core.QueryBuilder;
import core.Services;
import core.Speakable;
import core.UserProfile;
import utils.Helpers;

import java.util.ArrayList;
import java.util.List;

// --- Static Analysis Expected ---
//
// Defined entities:
//   Class (1): Main
//   Methods (1): main(String[])
//
// Edge cases covered:
//   - Entry point calling constructors across packages
//   - Cross-package method calls (core, utils)
//   - Interface static method call: Speakable.defaultGreeting()
//   - Static factory call: AnimalFactory.create(...)
//   - Record instantiation: new UserProfile(...)
//   - Enum usage: Config.DEBUG.format(...)
//   - Static nested class instantiation: new Container.Metadata(...)
//   - Inner class instantiation: container.new Item(...)
//   - Builder pattern chaining: new QueryBuilder().from().where()...
//   - Lambda expression as argument
//   - Method reference usage via Services
//   - Varargs call: Helpers.sum(1, 2, 3, 4, 5)
//
// Expected call edges:
//   main(String[]) → Dog(String, int)
//   main(String[]) → Cat(String)
//   main(String[]) → Duck(String)
//   main(String[]) → Dog.speak()
//   main(String[]) → Cat.purr()
//   main(String[]) → Duck.actions()
//   main(String[]) → Dog.describe()
//   main(String[]) → Speakable.defaultGreeting()
//   main(String[]) → AnimalFactory.create(String, String)
//   main(String[]) → AnimalFactory.create(String, String, int)
//   main(String[]) → UserProfile(String, String)
//   main(String[]) → UserProfile.displayName()
//   main(String[]) → Config.format(String)
//   main(String[]) → Container(String)
//   main(String[]) → Container.Metadata(String, String)
//   main(String[]) → Container.Item(String)
//   main(String[]) → Metadata.format()
//   main(String[]) → Item.describe()
//   main(String[]) → QueryBuilder()
//   main(String[]) → from(String), where(String), orderBy(String), limit(int), build()
//   main(String[]) → Services.processAnimals(...)
//   main(String[]) → Services.getNames(...)
//   main(String[]) → Services.createDogs(...)
//   main(String[]) → Services.filterAnimals(...)
//   main(String[]) → Services.describeAll(...)
//   main(String[]) → Services.nameComparator()
//   main(String[]) → Services.getAnimalCity(...)
//   main(String[]) → Services.computeTotal(...)
//   main(String[]) → Services.buildQuery(...)
//   main(String[]) → Helpers.add(int, int)
//   main(String[]) → Helpers.clamp(int, int, int)
//   main(String[]) → Helpers.formatName(String, String)
//   main(String[]) → Helpers.sum(int...)
//
// Package dependencies:
//   app imports core
//   app imports utils
//
// ---

public class Main {

    public static void main(String[] args) {
        // Constructor calls — single inheritance, constructor chaining
        Dog dog = new Dog("Rex", 5);
        Cat cat = new Cat("Whiskers");
        Duck duck = new Duck("Donald");

        // Instance method calls — method overriding
        System.out.println(dog.speak());
        System.out.println(cat.purr());
        System.out.println(duck.actions());

        // Interface default method call
        System.out.println(dog.describe());

        // Interface static method call
        System.out.println(Speakable.defaultGreeting());

        // Static factory — overloaded methods, switch expression
        Animal factoryDog = AnimalFactory.create("dog", "Buddy");
        Animal factoryAnimal = AnimalFactory.create("dog", "Max", 3);

        // Record instantiation and method call
        UserProfile profile = new UserProfile("Alice", "alice@example.com");
        System.out.println(profile.displayName());

        // Enum with abstract method per constant
        Config config = Config.DEBUG;
        System.out.println(config.format("Starting..."));

        // Nested classes — static nested and inner class
        Container container = new Container("box");
        Container.Metadata meta = new Container.Metadata("color", "red");
        Container.Item item = container.new Item("toy");
        System.out.println(meta.format());
        System.out.println(item.describe());

        // Builder pattern — method chaining
        String query = new QueryBuilder()
                .from("animals")
                .where("type = 'dog'")
                .orderBy("name")
                .limit(5)
                .build();
        System.out.println(query);

        // Lambdas, streams, method references via Services
        List<Animal> animals = new ArrayList<>();
        animals.add(dog);
        animals.add(cat);
        animals.add(duck);

        Services.processAnimals(animals);
        List<String> names = Services.getNames(animals);
        List<Dog> dogs = Services.createDogs(List.of("A", "B", "C"));

        // Lambda as Predicate argument
        List<Animal> filtered = Services.filterAnimals(animals, a -> a.getName().length() > 3);

        // Generics with bounded type
        String description = Services.describeAll(animals);

        // Anonymous class via factory method
        animals.sort(Services.nameComparator());

        // Optional chaining
        String city = Services.getAnimalCity(dog);

        // Cross-module utility calls
        int total = Services.computeTotal(5, 10, 0, 100);
        int sum = Helpers.add(1, 2);
        int clamped = Helpers.clamp(50, 0, 100);
        String name = Helpers.formatName("John", "Doe");
        int varargSum = Helpers.sum(1, 2, 3, 4, 5);

        // Builder via service
        String queryViaService = Services.buildQuery("users");
    }
}
