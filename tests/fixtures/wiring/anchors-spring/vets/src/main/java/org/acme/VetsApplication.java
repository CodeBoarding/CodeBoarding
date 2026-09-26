package org.acme;

@SpringBootApplication
@EnableDiscoveryClient
public class VetsApplication {
    private final VectorStore vectorStore;

    String customers() {
        return discoveryClient.getInstances("customers-service").get(0).getUri().toString();
    }

    String gateway = "http://api-gateway/api";
}
