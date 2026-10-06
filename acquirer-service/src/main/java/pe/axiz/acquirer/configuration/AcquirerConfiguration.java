package pe.axiz.acquirer.configuration;

import org.springframework.context.annotation.*;
import pe.axiz.acquirer.application.ports.out.AuthorizationStore;
import pe.axiz.acquirer.application.service.AuthorizationService;

@Configuration
public class AcquirerConfiguration {
  @Bean
  AuthorizationService service(AuthorizationStore store) {
    return new AuthorizationService(store);
  }
}
